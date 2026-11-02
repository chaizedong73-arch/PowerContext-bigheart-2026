# 技术说明（TECHNICAL NOTE）

## 1. 系统定位

本模块解决软件持续演进中的三个上下文难题：

1. **规有所依**：任务执行时只使用"当前有效"的规格版本。规格以版本化方式入库（`ctxsdd_specs`，chunk 元数据带 `version`），检索后按 source 过滤到最新版本——规格 v2 重新导入后，同一 source 的 v1 块自动被过滤，上下文与模型输出均使用 v2 内容。
2. **来源可溯**：上下文包（context pack）中每条规格/代码/测试都带来源标注（seekdb 集合、chunk_id、文件路径、版本号），写入 `docs/runs/context_pack.json`。
3. **状态可复用**：所有命令是独立进程，共享 `.ctxsdd/powercontext_state.json`（并镜像到 `docs/runs/`）。任务记录、规格版本、代码索引在模块重启后仍可读取，用于后续任务处理。

## 2. 架构

```
规格 Markdown ──chunker──> seekdb(ctxsdd_specs)
业务代码/测试 ──────────> seekdb(ctxsdd_code / ctxsdd_tests)
                                  │
run-task: 任务描述 ──嵌入检索──> 最新版规格块 + 相关代码/测试
                                  │
                    contextpack（带来源标注）──> prompt
                                  │
                    MiniMax chatcompletion_v2（真实调用，minimax_dev_log.jsonl）
                                  │
                    patch（JSON 解析/路径校验/落盘）──> runner(pytest)
                                  │
                    未通过 → 带失败信息重试（≤max-rounds）；通过 → 状态落盘
```

### 模块职责

| 模块 | 职责 |
| --- | --- |
| `chunker.py` | 按 Markdown 标题切块，chunk_id 含内容哈希，元数据带 source/version/heading |
| `obstore.py` | pyseekdb 嵌入式客户端；3 个集合；嵌入器自动探测（chroma 默认 → sentence-transformers → 确定性哈希回退），`CTXSDD_EMBEDDER` 可强制指定 |
| `contextpack.py` | 组装带来源标注的上下文包与 prompt；检索缺失的任务目标文件直接纳入并标注 `task_target` |
| `minimax_client.py` | MiniMax 开放平台真实调用；API Key 只读环境变量 `MINIMAX_API_KEY`；每次请求/响应/用量记录进 `minimax_dev_log.jsonl`；`base_resp.status_code` 非 0 时抛错（1008 余额不足给出提示） |
| `patch.py` | 模型输出必须是 `{"summary", "files":[{"path","content"}]}`；解析（围栏/裸 JSON 双通道）、路径校验（仅允许 `business/`、拒绝越界）、落盘；`unified_diff` 生成代码差异记录 |
| `runner.py` | subprocess 运行 pytest，junitxml 解析逐用例结果；每次运行追加进 `test_run_log.jsonl` |
| `state.py` | 任务状态持久化 + `docs/runs/powercontext_state.json` 镜像 |

## 3. 关键设计决策

- **模型产出用"整文件 JSON"而非 unified diff**：整文件回填对弱格式容忍度更高，避免 diff 上下文不匹配导致的应用失败；路径白名单（`business/` 前缀、拒绝 `..`）防止越权写入。代码差异由本地 `difflib.unified_diff` 生成留档，"模型产出的修改与提交差异对应"通过 `code_diff.txt` + `minimax_dev_log.jsonl` 共同证明。
- **多轮修复**：run-task 每轮结束后跑目标测试 + 回归测试；未通过则把失败用例与输出尾部回传模型再修（默认 ≤3 轮），全程留痕。
- **修改前基线**：run-task 在调用模型前先跑一次测试并记录——目标测试"修改前失败、修改后全过"的证据链由此闭环（`test_run_log.jsonl` 中 `task_N:before` 与 `task_N:roundK` 记录）。
- **嵌入器三级回退**：保证在无网络、无模型缓存的评审环境也能运行（哈希词袋嵌入，384 维确定性输出），同时优先使用语义嵌入提升检索质量。
- **数据库本地盘约束**：嵌入式 seekdb 要求数据库目录位于本地文件系统（`/mnt/c` 等 9p 挂载路径会 `seekdb_open failed`），`.ctxsdd/seekdb` 默认在工程根目录下，随工程一起拷贝即可。

## 4. 证据链（docs/runs/）

| 文件 | 证明内容 |
| --- | --- |
| `minimax_dev_log.jsonl` | MiniMax 实际参与（每次调用的完整请求/响应/用量） |
| `ob_query_log.jsonl` | seekdb 实际参与（查询文本、命中、用途） |
| `context_pack.json` / `context_pack_N.json` | 上下文来源标注；规格更新后使用 v2 内容 |
| `spec_update/` | 规格 v1/v2 原文与前后对照 diff |
| `test_run_log.jsonl` | 修改前失败 / 修改后全过 / 回归全过 |
| `code_diff.txt` / `code_diff_N.txt` | 模型产出与代码差异的对应关系 |
| `powercontext_state.json` | 状态跨进程持久化与复用 |
| `docs/snapshots/` | 修改后代码快照 |

## 5. 已知边界

- 业务工程的修改只允许发生在 `business/` 下；如需扩展可修改范围，调整 `patch.py` 的 `ALLOWED_PREFIX`。
- run-task 的 `--targets` 指定目标测试文件；目标文件除 seekdb 检索外强制纳入上下文（标注 `task_target`），避免弱检索漏包。
- MiniMax 余额不足（1008）时 run-task 优雅退出（exit 2），不影响已完成任务的持久化状态。

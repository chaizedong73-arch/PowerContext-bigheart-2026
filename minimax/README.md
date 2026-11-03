# 规有所依 —— 面向软件持续演进的 SDD 上下文管理系统（MiniMax 赛题）

本目录是为 AI 开发助手实现的工程上下文模块 `ctxsdd`：按开发任务提供**当前有效的规格、相关代码和测试**，调用 MiniMax 大模型完成真实代码修改，并用测试验证修改有效。整个流程由 seekdb（嵌入式 OceanBase 生态向量库）保存工程资料索引，任务状态跨进程持久化、重启模块后可复用。

## 目录结构

```
minimax/
├── ctxsdd/                    # SDD 上下文管理模块
│   ├── cli.py                 # 命令行：init / import-code / import-spec / run-task / verify / tasks
│   ├── chunker.py             # 规格按标题切块（带来源与版本）
│   ├── obstore.py             # pyseekdb 存储/检索（ctxsdd_specs / ctxsdd_code / ctxsdd_tests）
│   ├── contextpack.py         # 带来源标注的上下文打包
│   ├── minimax_client.py      # MiniMax 真实 API 调用（记录开发日志）
│   ├── patch.py               # 模型产出解析/校验/落盘 + unified diff 生成
│   ├── runner.py              # pytest 执行与结果解析
│   └── state.py               # 任务状态持久化（重启可复用）
├── business/reading_list/     # 业务工程（读书清单）与三组测试
│   ├── reading_list.py        # 提交时为"修改前"状态，由 run-task 在运行中修改
│   └── tests/
│       ├── test_regression.py       # 回归基线（4 项，始终应通过）
│       ├── test_target_features.py  # 目标功能测试（3 项，实现前失败）
│       └── test_spec_update.py      # 规格 v2 行为测试（2 项，更新前失败）
├── specs/                     # 规格 v1 与 v2（用于验证"规格更新并重新导入"）
├── tasks/                     # 开发任务描述文件
├── scripts/run_e2e.sh         # 一键端到端脚本
└── docs/
    ├── runs/                  # 运行证据（日志、状态、上下文包、代码差异、测试记录）
    └── snapshots/             # 修改后代码快照
```

## 运行说明

环境要求：Linux x86_64（或 WSL2）、Python 3.10+、可访问 `api.minimax.io`。数据库为嵌入式 seekdb（pyseekdb），无需部署服务；数据库目录必须在本地文件系统（不能放在 `/mnt/c` 等网络/挂载路径）。

```bash
cd minimax
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# MiniMax API Key（只从环境变量读取，不写入任何文件）
export MINIMAX_API_KEY=sk-...        # 需要有效余额
export HF_ENDPOINT=https://hf-mirror.com   # 可选：首次下载嵌入模型走镜像

# 可选：站点与模型覆盖（国际站默认 https://api.minimax.io；国内站示例）
# export CTXSDD_MINIMAX_API_URL=https://api.minimaxi.com/v1/text/chatcompletion_v2
# export CTXSDD_MINIMAX_MODEL=MiniMax-M3.1-Flash

bash scripts/run_e2e.sh
```

也可分步执行：

```bash
python -m ctxsdd init                                            # 空状态初始化
python -m ctxsdd import-code                                     # 代码/测试索引进 seekdb
python -m ctxsdd import-spec specs/reading_list_spec_v1.md --version 1
python -m ctxsdd run-task --task-file tasks/01_implement_features_v1.md \
  --targets business/reading_list/tests/test_target_features.py
python -m ctxsdd import-spec specs/reading_list_spec_v2.md --version 2   # 规格更新重导入
python -m ctxsdd run-task --task-file tasks/02_apply_spec_v2.md \
  --targets business/reading_list/tests/test_spec_update.py
python -m ctxsdd verify                                          # 全量测试 + 状态复用展示
python -m ctxsdd tasks                                           # 查看持久化任务记录
```

嵌入器自动探测（chroma 默认嵌入 → sentence-transformers → 确定性哈希回退），可用 `CTXSDD_EMBEDDER=default|st|hash` 强制指定，保证无网络环境也可运行（`hash` 模式无需下载模型）。

## 验收项对照

| 验收要求 | 本实现 |
| --- | --- |
| 能够运行 | 上面的运行说明可直接初始化数据、启动模块、执行验证样例；除 MiniMax API Key 与 pip 依赖外不依赖未交付的本地文件或状态 |
| MiniMax 实际参与 | `run-task` 真实调用 MiniMax（chatcompletion_v2），每次请求/响应记录进 `docs/runs/minimax_dev_log.jsonl`；模型产出的修改落盘并体现在 `docs/runs/code_diff*.txt` 与 `docs/snapshots/` |
| 上下文正确 | `docs/runs/context_pack*.json` 每个条目带来源（seekdb 集合 / chunk_id / 规格版本 / 文件路径）；规格 v2 重新导入后，查询结果过滤到最新版本，上下文与模型修改均使用 v2 内容（见 `docs/runs/spec_update/` 前后对照） |
| 数据库实际参与 | seekdb 保存规格块、代码、测试的向量索引；`run-task` 的每次查询与命中结果记录进 `docs/runs/ob_query_log.jsonl`，实际查询结果构成上下文包 |
| 状态能够复用 | 任务记录保存在 `.ctxsdd/powercontext_state.json` 并镜像到 `docs/runs/powercontext_state.json`；每个 CLI 命令都是独立进程，后续命令读取先前保存的任务（`verify`/`tasks` 输出可见） |
| 代码修改有效 | `run-task` 先跑基线（目标测试失败），MiniMax 修改后再跑：目标测试全过、回归测试全过；全过程记录在 `docs/runs/test_run_log.jsonl` |

## 证据文件（docs/runs/，由真实运行生成）

- `minimax_dev_log.jsonl` — MiniMax 每次调用的请求/响应/用量
- `ob_query_log.jsonl` — seekdb 每次检索的查询文本、命中与用途
- `context_pack.json` 及 `context_pack_N.json` — 带来源标注的上下文包
- `test_run_log.jsonl` — 修改前基线、每轮修改后、verify 的全部测试结果
- `powercontext_state.json` — 任务状态（跨进程复用）
- `code_diff.txt`、`code_diff_N.txt` — 任务内与累计 unified diff
- `spec_update/` — 规格 v1/v2 原文与更新对照 diff

## 设计说明

详见 [TECHNICAL_NOTE.md](TECHNICAL_NOTE.md)。

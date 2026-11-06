# 验证样例：个人复习教练 RecallLoop

提交材料要求「验证样例：初始数据 + 输入 + 步骤 + 预期结果」，并展示 seekdb / PowerContext 真实发挥作用。本文件即验证样例正文；第 5 节的执行记录表在阶段 5 由实际运行结果回填。

## 1. 初始数据

两份 Markdown 资料（也可替换为用户自己的讲义/笔记）：

| 文件 | 内容 | 用途 |
| --- | --- | --- |
| `demo-data/os-basics.md` | OceanBase/存储基础笔记（约 10 个标题分节） | 主要验证资料 |
| `demo-data/study-notes.md` | 课程笔记（含 3 处易混概念） | 薄弱点验证 |

> 阶段 5 在 Qoder 中生成这两份文件到 `qoder/app/demo-data/`；内容自拟，但须覆盖上表主题。

## 2. 验证步骤与预期结果

### V1 资料导入 + seekdb 索引

- **输入**：`POST /api/documents` 两次，分别导入上面两份文件。
- **步骤**：资料库页上传 → 查看列表。
- **预期**：返回 `chunks > 0`；资料列表出现两个文件及块数；`qoder/app/data/seekdb/` 目录生成（嵌入式 seekdb 数据库）。

### V2 混合检索问答（带出处）

- **输入**：问答页提问「seekdb 和 PowerContext 在本项目中如何分工？」（对应 os-basics.md 中小节）。
- **预期**：Top-5 依据卡命中正确小节，每卡显示 `source: os-basics.md` 与标题；`data/retrieval_log.jsonl` 新增一行，含 vector 与 keyword 信号。
- **再输入**：同一问题 + 来源过滤 `os-basics.md`，预期结果全部来自该文件。

### V3 复习自测 + PowerContext 记忆写入

- **输入**：复习页生成 5 张卡，自评：2 张「会」、2 张「不会」（选 os-basics 相关）、1 张「模糊」。
- **预期**：复习页统计正确率 40%；`data/memory_log.jsonl` 出现 ≥2 条 `POST /v1/memory/remember`（薄弱点）与 5 条 `/v1/sources/content` 留底记录；用 `curl -X POST http://127.0.0.1:8000/v1/memory/search -d '{"scope_id":"<SCOPE>","query":"薄弱"}'` 能查到刚写入的薄弱点记忆。

### V4 跨会话续接（PowerContext 核心价值）

- **步骤**：完全停止应用进程并重启（PowerContext Server 保持运行）→ 打开进度页。
- **预期**：「上次复习进度」非空（context/prepare 返回 `status: "ready"`）；「今日推荐清单」以 V3 标记「不会」的知识点来源优先；页面显示的记忆状态为 ready。
- **对照组**：换一个全新 scope_id 打开，进度页显示「首次使用，还没有历史记录」（status=empty）——证明内容确实来自 PowerContext 而非本地缓存。

## 3. 技术发挥作用记录法（提交时随验证样例说明）

| 技术 | 证据 | 取证方式 |
| --- | --- | --- |
| seekdb（pyseekdb 嵌入式） | `data/retrieval_log.jsonl`：每次问答的向量得分、关键词得分、Top-5 出处 | 截图日志文件 + 问答页依据卡 |
| PowerContext Memory | `data/memory_log.jsonl`：remember/search/sources 调用与响应 | 截图日志 + curl 返回 |
| PowerContext 续接 | V4 重启后进度页出现上次进度 | 截图（重启前后） |

## 4. 效果数据（鼓励项）

- 自用 7 天，记录：导入资料数、问答次数、复习卡数、次日续接成功率。
- 提效对比：同一批资料「不使用工具找回一个知识点」vs「在问答页 10 秒带出处定位」的时间对比记录。
- 若发布公网，附 Qoder Sites 访问统计截图。

**发布与访问数据（2026-11-07 核对回填）：**

- 公网地址：`https://recallloop-coach-g913y85jmem.qoder.zone`（Qoder Sites，access_mode=public，任何人都可访问）。
- 线上内容核对：`index.html` / `app.js` / `styles.css` 与仓库 `qoder/app/static/` 当前提交逐字节一致（仅平台注入水印脚本与换行符差异）；演示模式（后端未连接自动降级）匿名可访问。
- Qoder Sites 访问统计：上线至今 PV=4（平台口径 `coverage: partial`，采集不全，仅作量级参考）。
- 六项必备内容均在「关于」页：项目简介 / 解决的问题 / 核心功能 / 演示（内置数据真实渲染）/ 代码仓库链接 / 体验入口。

## 5. 执行记录（阶段 5 回填）

运行环境：WSL2 Ubuntu，pyseekdb 1.4.0.post1（嵌入式，`SEEKDB_PATH=/home/chaizedong/recallloop-data/seekdb`，非 /mnt/c）；PowerContext Server 1.2.0（`http://127.0.0.1:8000`）；应用 uvicorn `127.0.0.1:8100`；scope `scp_4y5bypa0y6njp369ykn7btggvt`；嵌入器 local（384 维确定性哈希，无 API Key）。

| 编号 | 执行时间 | 结果 | 证据文件 |
| --- | --- | --- | --- |
| V1 | 2026-11-04 04:56 (+0800) | 通过：导入 `os-basics.md`(11 块) + `study-notes.md`(5 块)，`GET /api/documents` 返回两文件及块数；嵌入式 seekdb 数据目录已生成 | `data/seekdb/`（WSL 原生）；`GET /api/documents` 响应 |
| V2 | 2026-11-04 05:00:23 (+0800) | 通过：提问「seekdb 和 PowerContext 在本项目中如何分工？」Top-5 首卡命中《两组件分工》(score 0.4061 = vector 0.2325 + keyword 0.6667)，全部带 `source: os-basics.md`；来源过滤后结果全来自该文件 | `data/retrieval_log.jsonl`（含 vector/keyword 信号） |
| V3 | 2026-11-04 05:00 (+0800) | 通过：生成 5 卡自评 2 会/2 不会/1 模糊（正确率 40%）；`memory_log.jsonl` 记 3 条 `/v1/memory/remember` + 5 条 `/v1/sources/content`；`curl /v1/memory/search {"query":"薄弱"}` 返回 3 条命中 | `data/reviews.json`；`data/memory_log.jsonl` |
| V4 | 2026-11-04 05:00:30 (+0800) | 通过：停止并重启应用进程（PowerContext 保持运行）后，进度页 `/v1/context/prepare` 返回 `status:"ready"`(1288 bytes)，今日清单以薄弱来源 `os-basics.md` 优先；对照组全新 scope 返回 `status:"empty"`（首次使用） | `data/memory_log.jsonl`（context/prepare ready）；重启前后进度页 |

> 回归补充：并发压测 16 路并行请求（ask+documents+plan）全部返回正确非空数据——已修复嵌入式 seekdb 客户端在 uvicorn 线程池下的线程安全问题（`store.py` 全局 `threading.RLock`）。机械挖空出题经两轮优化，优先挖数字/英文专名（如 OceanBase、seekdb），冷启动后单次出题 0.17s。

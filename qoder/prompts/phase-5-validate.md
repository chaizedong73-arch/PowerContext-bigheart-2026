# 阶段 5：调试验证（跑通验证样例）

> 前置：阶段 4 完成。本阶段对着 `qoder/VALIDATION.md` 的验证样例逐条执行，把遇到的问题交给 Qoder 修，**每修一个问题都截图**（这也是 Qoder 开发记录的一部分）。

---

请帮我执行集成验证并修复问题。仓库里有验证样例文件 `qoder/VALIDATION.md`，按其中的步骤逐条执行：

1. 在 WSL2/Linux 里启动 PowerContext Server（`powercontext server run`）和应用（`uvicorn main:app --app-dir qoder/app --port 8100`）。
2. 按 `qoder/VALIDATION.md` 第 2 节的步骤 V1–V4 逐条执行，每条记录：实际结果、与预期是否一致。
3. 重点核对 VALIDATION 第 4 节的「技术发挥作用证据」：
   - `data/retrieval_log.jsonl` 有每次问答的三路检索记录；
   - `data/memory_log.jsonl` 有 memory/remember、memory/search、context/prepare 的调用记录；
   - 用 curl 直接调 `http://127.0.0.1:8000/v1/memory/search` 能查到我们写入的薄弱点记忆。
4. 常见问题按此排查顺序：
   - pyseekdb 报 wheel/平台错误 → 确认在 Linux 虚拟环境运行、Python 版本；
   - seekdb 报 IO/锁定错误 → 确认 `SEEKDB_PATH` 不在 `/mnt/c` 等挂载路径；
   - PowerContext 请求 401 → 确认 `POWERCONTEXT_TOKEN` 环境变量与 Server 启动配置一致；
   - context/prepare 返回 empty → 确认 scope_id 与写入时一致、且已有 remember 记录；
   - 中文检索效果差 → 调整 local embedding 的分词（加入中文 2-gram）与关键词打分权重。
5. 发现的 bug 直接修复；修复后把 V1–V4 **全部重跑一遍**，保证没有回归。
6. 全部通过后，把实际执行结果回填到 `qoder/VALIDATION.md` 的「执行记录」表格里（时间、结果、证据文件名）。

完成后输出：V1–V4 执行结果表、修复过的问题清单、最终三份证据文件（retrieval_log / memory_log / 截图）的存在确认。

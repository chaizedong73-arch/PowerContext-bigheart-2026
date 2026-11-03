# 阶段 1：项目脚手架

> 使用方法：在 Qoder 中打开本仓库（`2026-competition-ecosystem` 分支），把下面整段提示词发给 Qoder Agent。完成后自测并截图存档，再进入阶段 2。

---

请在本仓库的 `qoder/app/` 目录下创建「个人复习教练」应用的脚手架。这是一个帮助用户复习的学习工具 Web 应用，本阶段只搭骨架、跑通健康检查。

要求：

1. 技术栈：
   - 后端：Python 3.11 + FastAPI + uvicorn，入口 `qoder/app/main.py`。
   - 前端：`qoder/app/static/` 下的单页应用（原生 HTML/CSS/JS 即可，不要引入构建工具），由 FastAPI StaticFiles 挂载到 `/`。
2. 后端先实现以下空壳路由（返回固定 JSON，阶段 2/3 再填实现）：
   - `GET /api/health` → `{"status": "ok"}`
   - `POST /api/documents` （资料导入，占位）
   - `GET /api/documents` （资料列表，占位）
   - `POST /api/ask` （检索问答，占位）
   - `POST /api/review/answer` （自评提交，占位）
   - `GET /api/plan` （今日复习计划，占位）
3. 前端单页包含四个标签页的空壳：资料库 / 问答 / 复习 / 进度。顶部显示应用名「复习教练 RecallLoop」。
4. 配置统一放在 `qoder/app/config.py`：
   - `SEEKDB_PATH`（默认 `./data/seekdb`，从环境变量 `SEEKDB_PATH` 读取）
   - `POWERCONTEXT_URL`（默认 `http://127.0.0.1:8000`，从环境变量读取）
   - `POWERCONTEXT_SCOPE_ID`（从环境变量读取，可为空）
   - `EMBEDDING_PROVIDER`（`local` 或 `api`，默认 `local`）
   禁止把任何密钥写进代码或配置文件，只允许从环境变量读取。
5. 新建 `qoder/app/requirements.txt`：fastapi、uvicorn[standard]、pydantic。**不要**在本阶段安装 pyseekdb（阶段 2 再加）。
6. 新建 `qoder/app/README.md`，写明启动方式（开发环境为 WSL2/Linux，见仓库 RUNBOOK）。
7. 运行 `uvicorn main:app --app-dir qoder/app --port 8100`，验证 `GET /api/health` 返回 ok、首页可打开。

完成后输出：目录树、启动命令、验证结果。

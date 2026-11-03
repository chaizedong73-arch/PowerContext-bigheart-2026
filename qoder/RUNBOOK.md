# 运行说明（模板，提交前由实际项目补全）

项目：个人复习教练 RecallLoop（Qoder 赛题）
仓库：<提交时填最终仓库地址>，分支 `2026-competition-ecosystem`，代码目录 `qoder/app/`

## 1. 环境要求

| 组件 | 版本/要求 | 说明 |
| --- | --- | --- |
| OS | Linux x86_64（**WSL2 即可**） | pyseekdb wheel 仅提供 Linux 版 |
| Python | 3.11+ | 后端 |
| uv | 最新 | 安装 PowerContext CLI/Server |
| Node/浏览器 | 现代浏览器 | 前端为静态单页，无需构建 |

## 2. 部署 seekdb（嵌入式）

本项目使用 **pyseekdb 嵌入式模式**，seekdb 以库形式内嵌在应用进程中，无需单独部署服务：

```bash
# 在 WSL2/Linux 的虚拟环境中
python3 -m venv .venv && source .venv/bin/activate
pip install pyseekdb
```

- 数据库目录由环境变量 `SEEKDB_PATH` 指定（默认 `qoder/app/data/seekdb`），首次导入资料时自动创建集合 `review_docs`。
- **注意**：数据库目录必须位于本地文件系统，不要放在 `/mnt/c` 等挂载路径。

## 3. 部署接入 PowerContext

```bash
uv tool install --force "powercontext[cli,server]==1.2.0"
powercontext server run          # 默认 http://127.0.0.1:8000
# 验证
powercontext doctor
curl http://127.0.0.1:8000/health/live
```

应用侧通过 HTTP API 使用：

- `POST /v1/scopes`：首次启动自动创建 Scope（idempotency_key=recallloop-app），scope_id 持久化到 `data/scope.json`；
- `POST /v1/memory/remember`：写入薄弱点（observation）与复习偏好（decision）；
- `POST /v1/memory/search`：出题时检索薄弱点；
- `POST /v1/context/prepare`：每次打开应用取回历史上下文实现跨会话续接。

如 Server 开启鉴权，设置环境变量 `POWERCONTEXT_TOKEN`（仅进程环境，不写入文件）。

## 4. 启动应用

```bash
source .venv/bin/activate
pip install -r qoder/app/requirements.txt

export POWERCONTEXT_URL=http://127.0.0.1:8000
export SEEKDB_PATH=$(pwd)/qoder/app/data/seekdb
# 可选：
# export EMBEDDING_PROVIDER=api
# export EMBEDDING_API_KEY=...   # 仅环境变量注入，不写入仓库

uvicorn main:app --app-dir qoder/app --port 8100
# 浏览器打开 http://127.0.0.1:8100
```

## 5. 验证

按 `qoder/VALIDATION.md` 的 V1–V4 执行；预期结果与证据取法见该文件。

## 6. 目录结构（提交前由实际项目核对）

```
qoder/app/
├── main.py           # FastAPI 入口
├── config.py         # 环境变量配置
├── embedder.py       # Embedding 抽象（local/api）
├── memory.py         # PowerContext 客户端封装
├── static/           # 前端单页 + 站点首页
├── demo-data/        # 验证样例初始数据
└── data/             # 运行时生成：seekdb 库、日志（不入库的可选）
```

## 7. 已知限制

- local embedding 为确定性哈希实现，语义检索效果弱于真实向量模型；配置 `EMBEDDING_PROVIDER=api` 后可达生产效果。
- 出题在无 Key 模式下为模板挖空，非模型生成。
- （提交前补充实际运行中发现的其他限制。）

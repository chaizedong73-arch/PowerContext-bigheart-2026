# 复习教练 RecallLoop — 应用说明

OceanBase 大赛 Qoder 赛题「Build with Qoder」作品。**知识检索交给 seekdb，学习记忆交给 PowerContext**，应用专注复习闭环体验。全部代码在 Qoder 中编写。

## 启动（开发环境为 WSL2/Linux）

> pyseekdb 的 wheel 仅支持 Linux x86_64，seekdb 数据目录必须在本地文件系统（不要放 `/mnt/c`）。详见仓库 `qoder/RUNBOOK.md`。

```bash
# 1) 虚拟环境 + 依赖
python3 -m venv .venv && source .venv/bin/activate
pip install -r qoder/app/requirements.txt

# 2) 启动 PowerContext Server（另开终端，一次性）
uv tool install --force "powercontext[cli,server]==1.2.0"
powercontext server run            # http://127.0.0.1:8000

# 3) 启动应用
export POWERCONTEXT_URL=http://127.0.0.1:8000
export SEEKDB_PATH=$HOME/recallloop-data/seekdb   # 本地文件系统，勿放 /mnt/c
uvicorn main:app --app-dir qoder/app --port 8100
# 浏览器打开 http://127.0.0.1:8100
```

无 Key 即可全流程运行（local 确定性哈希 embedding + 机械挖空出题）。PowerContext Server 未启动时，进度页会给出友好提示，复习/问答仍可用。

## 配置（全部走环境变量，密钥绝不入仓库）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SEEKDB_PATH` | `qoder/app/data/seekdb` | 嵌入式 seekdb 数据库目录（须在本地文件系统） |
| `POWERCONTEXT_URL` | `http://127.0.0.1:8000` | PowerContext Server 地址 |
| `POWERCONTEXT_SCOPE_ID` | 空 | 空则自动创建并持久化到 `data/scope.json` |
| `POWERCONTEXT_TOKEN` | 空 | Server 开启鉴权时的 Token（仅进程环境） |
| `EMBEDDING_PROVIDER` | `local` | `local`（无 Key）或 `api`（OpenAI 兼容） |
| `EMBEDDING_API_KEY/BASE/MODEL` | 空 | 仅 `api` 模式用，Key 不落盘 |

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| POST | `/api/documents` | 导入资料（切块 + seekdb 索引） |
| GET | `/api/documents` | 资料列表 |
| DELETE | `/api/documents/{filename}` | 删除某资料 |
| POST | `/api/ask` | 三路混合检索问答（带出处） |
| POST | `/api/review/cards` | 生成复习卡（薄弱点优先） |
| POST | `/api/review/answer` | 提交自评（写 PowerContext 记忆） |
| POST | `/api/preferences` | 保存复习偏好（decision 记忆） |
| GET | `/api/plan` | 今日复习计划（读记忆，跨会话续接） |

## 目录

```
qoder/app/
├── main.py        # FastAPI 入口与路由
├── config.py      # 环境变量配置
├── embedder.py    # Embedding 抽象（local 256 维哈希 / api）
├── store.py       # seekdb 封装：切块、写入、三路混合检索
├── memory.py      # PowerContext HTTP 客户端（记忆/续接）
├── review.py      # 出题、本地自评记录、今日计划
├── static/        # 前端单页（index.html / styles.css / app.js）
├── demo-data/     # 验证样例初始资料
└── data/          # 运行时生成：seekdb 库、日志、scope、自评记录
```

## 证据日志（提交用）

- `data/retrieval_log.jsonl`：每次问答的向量/关键词得分与 Top-5 出处（seekdb 发挥作用）。
- `data/memory_log.jsonl`：PowerContext 各端点调用与响应摘要（记忆发挥作用，不含 Token）。

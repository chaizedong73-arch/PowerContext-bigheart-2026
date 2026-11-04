# 端侧 AI 知识库：浏览器本地向量化 × seekdb 混合检索

2026 OceanBase 大赛（阶跃星辰赛道）：在浏览器中本地运行 embedding 模型完成文档向量化，向量、原文与元数据写入 seekdb，检索由 seekdb 内部完成全文 + 向量混合（RRF 融合）。无云端推理 API，全部推理在用户设备上执行。

## 架构

```
浏览器（端侧推理）                    Node 后端（数据层）
┌──────────────────────┐   HTTP    ┌──────────────────────┐
│ Transformers.js      │ ────────> │ Express + seekdb-js  │
│ ONNX Runtime Web     │           │                      │
│ all-MiniLM-L6-v2     │           │  嵌入式 seekdb        │
│ WebGPU→fp16 /        │  向量+文本 │  （Linux/macOS）      │
│ WASM→q8（INT8）      │ ────────> │  或 server 模式       │
│                      │           │  全文+向量 hybridSearch│
└──────────────────────┘           └──────────────────────┘
```

- 推理（文档向量化、查询向量化）100% 在浏览器本地执行（WebGPU 优先，自动降级 WASM 并在界面提示）。
- 存储与检索 100% 由 seekdb 完成：全文路 + 向量路 + RRF 融合均在数据库内执行，客户端不做两路结果拼合，不使用任何内存检索。

## 运行环境

- Node.js ≥ 20（嵌入式 seekdb 仅支持 Linux / macOS；Windows 请用 WSL 或 server 模式）
- Chrome / Edge 最新版（WebGPU 检索体验最佳；无 WebGPU 自动降级 WASM）

**模型与运行时已本地自托管**：`public/vendor/` 内置 transformers.js 4.3.2、ONNX Runtime Web 1.31.1 wasm（jsep/asyncify 双 flavor）与模型权重（fp16 43.2 MB / INT8 q8 21.9 MB，仓库自带；fp32 基线 86.2 MB 需执行 `bash scripts/download-models.sh` 补齐，默认走 hf-mirror.com）。页面运行期**零外网依赖**。

## 启动步骤（Linux / macOS / WSL）

```bash
cd stepfun
npm install
npm start          # 默认 http://localhost:5173
```

> WSL 注意事项（嵌入式 seekdb）：
> - 需要动态库 `libaio.so.1`（Ubuntu 24.04+ 包名 `libaio1t64`）：`apt-get download libaio1t64 && dpkg -x` 解包后把目录加入 `LD_LIBRARY_PATH`；
> - 嵌入式引擎的静态 TLS 需求较大，启动前设置 `GLIBC_TUNABLES=glibc.rtld.optional_static_tls=0x20000`，否则报 `cannot allocate memory in static TLS block`；
> - 数据文件（`SEEKDB_PATH`）不要放在 `/mnt/c` 等 Windows 挂载路径（OneDrive 同步会导致引擎卡死），放 WSL 本地文件系统（如 `~/seekdb-data/`）。

首次打开页面后：

1. 点击「加载模型」——模型文件已本地自托管，首次约 1.5–3.3 s（视精度），之后命中浏览器缓存更快。界面徽标显示当前后端（WEBGPU+fp16 或 WASM+q8）。
2. 点击「载入示例语料（48 篇）」→「开始导入」——浏览器本地批量向量化并写入 seekdb，界面显示进度与分阶段计时。
3. 在检索框输入查询——查询向量本地生成，seekdb 返回混合检索排序结果。

## seekdb 连接配置（环境变量）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SEEKDB_MODE` | `embedded` | `embedded`（进程内）或 `server`（远程 seekdb / OceanBase） |
| `SEEKDB_PATH` | `./data/seekdb.db` | 嵌入式数据目录 |
| `SEEKDB_DATABASE` | `knowledge_base` | 数据库名 |
| `SEEKDB_HOST` / `SEEKDB_PORT` / `SEEKDB_USER` / `SEEKDB_PASSWORD` / `SEEKDB_TENANT` | `127.0.0.1` / `2881` / `root` / 空 / 空 | server 模式连接参数 |

Windows 下使用 server 模式示例：

```powershell
$env:SEEKDB_MODE="server"; npm start
```

## 目录结构

```
stepfun/
├── server.js            # Express：静态托管 + REST（/api/ingest /api/search /api/collections …）
├── src/store.js         # seekdb 接入层：连接、集合、增量 upsert、索引刷新确认、hybridSearch
├── public/
│   ├── index.html / app.js / styles.css   # 前端（Transformers.js 端侧推理 + UI）
│   ├── sample/corpus.json                 # 48 篇示例语料（导入后 ID 为 doc-0001…doc-0048）
│   ├── eval/queries.json                  # 评测查询副本（浏览器端评测用）
│   └── vendor/                            # 本地自托管：transformers.js + ORT wasm + 模型权重
├── scripts/download-models.sh             # 模型/运行时获取脚本（hf-mirror.com，可重建 vendor/）
├── eval/
│   ├── queries.json                       # 14 条评测查询（标注相关文档 ID）
│   ├── results-webgpu-fp16.json 等        # 三配置浏览器端实测原始排序与计时
│   └── compute-metrics.mjs                # 指标计算：Recall@K / MRR@10 / nDCG@10 / 中位延迟
└── TECHNOTE.md          # 一页技术说明（模型与量化、端侧运行、seekdb 使用、性能实测）
```

## 检索质量复现（浏览器端评测）

加载模型 → 导入示例语料后，在控制台执行（或按 `eval/results-*.json` 的采集方式）：对 `eval/queries.json` 14 条查询逐条检索、记录排序，然后计算指标：

```bash
node eval/compute-metrics.mjs eval/results-webgpu-fp16.json eval/results-wasm-q8.json eval/results-wasm-fp32.json
```

实测结论（详见 TECHNOTE.md §1/§4）：INT8 量化质量零损失（Recall@10 96.4%、nDCG@10 0.9097 vs fp32 0.9084），体积 −75%；q8+WASM 端到端查询中位延迟 100 ms。

## API 摘要

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/state` | seekdb 连接模式与模型信息 |
| GET/POST/DELETE | `/api/collections` | 集合（会话）管理 |
| POST | `/api/ingest` | 增量导入：`{collection, records:[{id,text,embedding,metadata}]}`，返回写入/刷新耗时 |
| POST | `/api/search` | 混合检索：`{collection, query, embedding, topK}`，seekdb 内部 RRF 融合 |
| GET | `/api/documents` | 分页浏览已导入文档 |

详细设计与实测数据见 [TECHNOTE.md](TECHNOTE.md)。

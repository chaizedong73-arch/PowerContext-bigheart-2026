# 一页技术说明：端侧 AI 推理性能优化与 seekdb 混合检索

> 2026 OceanBase 大赛 · 阶跃星辰赛道 · 项目：浏览器本地向量化迷你知识库（seekdb 混合检索）
> 源码：`stepfun/` · 启动：`npm install && npm start`（Node ≥ 20，Linux/macOS/WSL；Windows 需 server 模式）

## 1. 模型与量化

| 项目 | 内容 |
| --- | --- |
| 模型 | `Xenova/all-MiniLM-L6-v2`（sentence-transformers/all-MiniLM-L6-v2 的 ONNX 转换版，Apache-2.0，384 维，Transformer 6 层蒸馏） |
| 来源与版本 | HuggingFace Hub `Xenova/all-MiniLM-L6-v2`（revision 751bff3），Transformers.js 4.3.2 按 `dtype` 加载对应 ONNX 文件；本项目将权重**预下载并本地自托管**在 `public/vendor/models/`（获取脚本 `scripts/download-models.sh`，默认走 hf-mirror.com） |
| 量化方式 | 官方仓库预转换权重：fp32（`model.onnx`）为量化前基线；量化后采用 **fp16**（`model_fp16.onnx`，WebGPU 路径）与 **INT8/q8**（`model_quantized.onnx`，WASM 路径），均为全量权重量化，未做再训练/校准 |
| 体积与压缩比 | 见下表（同一口径：本地文件实际字节数） |

| 精度 | 模型文件体积（实测） | 压缩比（vs fp32） |
| --- | --- | --- |
| fp32（基线） | 86.2 MB（model.onnx） | 1.0× |
| fp16 | 43.2 MB（model_fp16.onnx） | 2.0× |
| INT8（q8） | 21.9 MB（model_quantized.onnx） | 3.9×（−75%） |

**检索质量变化**：评测集为 `eval/queries.json` 的 14 条查询 + 48 篇语料（`public/sample/corpus.json`，ID 稳定为 doc-0001…doc-0048），在浏览器内对三种配置各跑全量 14 条混合检索并统计（原始排序见 `eval/results-*.json`，指标计算 `node eval/compute-metrics.mjs eval/results-*.json`）：

| 指标 | fp32 基线（WASM） | INT8（q8，WASM） | fp16（WebGPU） |
| --- | --- | --- | --- |
| Recall@5 | 90.5% | 90.5% | 90.5% |
| Recall@10 | 96.4% | 96.4% | 96.4% |
| MRR@10 | 0.9286 | 0.9286 | 0.9286 |
| nDCG@10 | 0.9084 | **0.9097** | 0.9084 |

结论：**INT8 量化后检索质量零损失**（Recall/MRR 与 fp32 完全一致，nDCG@10 反而 +0.0013，属排序噪声范围内），而模型体积 −75%。支撑「量化优先」的端侧部署策略：默认 WebGPU→fp16、WASM→q8。

## 2. 端侧运行

- 运行时：**Transformers.js 4.3.2（ONNX Runtime Web 1.31.1）**，feature-extraction pipeline，mean pooling + L2 归一化。运行时文件与 ORT wasm（jsep/asyncify 双 flavor，分别覆盖 WebGPU 与纯 WASM 路径）同样本地自托管于 `public/vendor/transformers/`，**运行期零外网依赖**（无 CDN、无 HF 请求），适配离线/内网演示。
- WebGPU 优先：启动时 `navigator.gpu.requestAdapter()` 显式检测（3 s 超时），可用则以 `device: "webgpu", dtype: "fp16"` 创建推理会话。
- WASM 自动降级：WebGPU 不可用或会话初始化失败时，自动以 `device: "wasm", dtype: "q8"` 重建管线；界面徽标明确显示当前后端（`WEBGPU` / `WASM`）与精度，并给出降级原因提示，核心流程不变。
- 推理位置：文档与查询向量化全部在浏览器内完成，不调用任何云端推理 API；Node 后端只接收「向量 + 原文」用于存储与检索，不执行任何模型计算。
- 验证：本机（Chrome 142，WebGPU 可用）徽标显示 WEBGPU + fp16；同一浏览器切换 device 下拉为 WASM 后徽标显示 WASM + q8，导入/检索全流程均可复现。

## 3. seekdb 使用

- 接入：`seekdb`（seekdb-js）1.4.0，`SeekdbClient` 双模式——嵌入式（`SEEKDB_PATH` 指向本地文件，Linux/macOS/WSL）或 server 模式（`SEEKDB_MODE=server` + host/port/user/password，Windows 或远程 OceanBase）。
- 集合结构：`getOrCreateCollection({ name, embeddingFunction: null, configuration: { dimension: 384, distance: "cosine" } })`——禁用 SDK 自带 embedding，向量全部由浏览器端提供；每条记录 = 稳定 ID（`doc-NNNN`）+ 原文（documents，供全文索引）+ 向量（embeddings）+ 元数据（title / index / source / importedAt）。集合名限 `[a-zA-Z0-9_]`。
- 混合检索：`collection.hybridSearch({ query: { whereDocument: { $contains: q } }, knn: { queryEmbeddings }, rank: { rrf: {} }, nResults })`——全文路与向量路均在 seekdb 内执行，RRF 融合后返回单一排序结果；客户端不做两路结果拼合，不使用内存检索替代。
- 增量写入与索引刷新：导入使用 `upsert`（稳定 ID，重复导入幂等，实测重复导入 count 不变）；写入后调用 `refresh_index()` 并轮询 `collection.get({ ids })` 直至全部新 ID 可见（超时 15 s 报错），实测 48 篇批量导入的刷新确认中位 **~430 ms**，保证新文档导入后立即可检索。
- 状态管理：集合即会话——前端将当前集合持久化到 `localStorage`，刷新页面自动恢复；支持多集合创建/切换/删除，连续导入与切换后检索结果始终与当前集合一致（每次请求显式携带 collection 名）。
- 已知问题与规避：① seekdb 1.4.0 发布的 `@seekdb/default-embed@1.4.0` peerDependencies 误含 `workspace:*`，package.json 用 overrides 钉到 1.2.0；② `client.listCollections()` 对 v2 集合内部异常被吞、恒返回空列表，本项目改为直接查询 seekdb 元数据表 `sdk_collections`（`client.execute(...)`，数据仍在 seekdb 内）。

## 4. 优化与性能实测

优化点：

1. **批处理**：文档向量化按批（32 篇/批）送入推理后端，减少调度开销；界面显示总耗时与吞吐。
2. **本地自托管 + 浏览器缓存**：模型与 ORT 运行时全部走本地静态服务，首次加载即无外网往返；文件经浏览器 HTTP 缓存持久化，二次加载免下载（实测 fp16 首次 3299 ms → 二次 2403 ms，q8 首次 1513 ms）。
3. **量化**：默认路径 fp16（WebGPU）/ q8（WASM），体积 −50% / −75%，质量零损失（见 §1）。
4. **写入与刷新**：批量 upsert + `refresh_index()` + 轮询确认，以确定性方式保证「写后即可检索」。
5. **检索**：单次 `hybridSearch` 往返完成全文 + 向量 + RRF，无客户端二次排序。

分阶段计时（界面「性能计时」面板自动记录、可导出 JSON；下表为预热后中位数，各配置首个查询的冷启动样本已剔除；查询规模 14 条、语料 48 篇）：

| 配置 | 模型体积 | 模型加载（首次含下载） | 文档向量化（48 篇） | 查询向量化（中位） | seekdb 混合检索（中位） | 端到端查询延迟（中位） | Recall@10 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| fp32 + WASM（基线） | 86.2 MB | 9199 ms | —（未单独测） | 20.5 ms | 173 ms | 254 ms | 96.4% |
| fp16 + WebGPU | 43.2 MB | 2403–3299 ms | 1797 ms（26.7 docs/s） | 153 ms | 12.6 ms | 232 ms | 96.4% |
| INT8(q8) + WASM | 21.9 MB | 1513 ms | 2353 ms（20.4 docs/s） | 18.9 ms | 31.4 ms | **100 ms** | 96.4% |

写入与刷新（48 篇批量 upsert）：fp16+WebGPU 会话 1928–1995 ms（其中刷新确认 427 ms）；q8+WASM 会话 3509 ms（刷新确认 438 ms）。

**实测环境与口径说明**：
- 环境：Windows 11 + WSL2（Node 22.21.1，seekdb 1.4.0 嵌入式，数据文件位于 WSL ext4），前端运行于 Electron 39 / Chromium 142 无头窗口。
- **该无头环境的 WebGPU 由 SwiftShader 软件渲染实现（无真实 GPU）**，因此 WebGPU+fp16 的向量化延迟（153 ms）反而高于 WASM+q8（18.9 ms）；在配备真实 GPU 的 Chrome/Edge 上，WebGPU 路径通常显著快于 WASM。两侧质量指标（§1）不受影响。
- seekdb 检索延迟波动较大（个位 ms ~ 数百 ms），主因是 WSL2 环境下嵌入式引擎的 CPU 调度与全文路径（IK 分词）开销；表中均为中位数。端侧部分（向量化）与库端部分（seekdb 检索）已分离计时，可分别归因。
- 计时口径：查询向量化 = 浏览器内 pipeline 推理（含池化/归一化）；seekdb 混合检索 = `/api/search` 服务端耗时；端到端 = 客户端发起请求至拿到结果。原始逐条数据见 `eval/results-*.json` 与界面导出 JSON。

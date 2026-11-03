# 阶段 2：资料导入 + seekdb 混合检索

> 前置：阶段 1 完成。本阶段是 seekdb 接入的核心，**API 用法以下方「已验证用法」为准，不要凭记忆编造 pyseekdb 接口**。在 Qoder 中执行后自测并截图存档。

---

为「复习教练 RecallLoop」接入嵌入式 seekdb，实现资料导入与三路混合检索。

## 一、依赖与环境

1. `qoder/app/requirements.txt` 增加 `pyseekdb`。
2. 注意：pyseekdb 的 wheel 只支持 **Linux x86_64**，请在 WSL2/Linux 的虚拟环境里安装运行（`pip install pyseekdb`）。
3. seekdb 数据库目录（`SEEKDB_PATH`，默认 `qoder/app/data/seekdb`）必须在**本地文件系统**上，绝不能放在 `/mnt/c` 等挂载路径，否则会报错。

## 二、Embedding 抽象

新建 `qoder/app/embedder.py`：

- 统一接口 `embed(texts: list[str]) -> list[list[float]]`。
- 两个实现：
  - `local`（默认，无 Key 可跑）：确定性哈希 embedding——对文本做分词 + n-gram 特征，映射到固定 256 维并 L2 归一化。必须保证同文本结果一致、语义无关文本向量接近正交。
  - `api`（可选）：读环境变量 `EMBEDDING_API_KEY`、`EMBEDDING_API_BASE`、`EMBEDDING_MODEL` 调用 OpenAI 兼容 embeddings 接口。Key 绝不落盘。

## 三、已验证的 pyseekdb 用法（严格照此编写）

参考实现见仓库 `minimax/ctxsdd/obstore.py`（该用法已在 WSL 环境实测通过）：

```python
import pyseekdb
from pyseekdb import HNSWConfiguration, Schema

DIM = 256  # 必须与 embedding 维度一致

client = pyseekdb.Client(path=str(db_path))          # 嵌入式，path 为本地目录
schema = Schema(vector_index=HNSWConfiguration(dimension=DIM, distance="cosine"))

# 建集合（幂等：先 get 失败再 create）
try:
    col = client.get_collection("review_docs")
except Exception:
    client.create_collection(name="review_docs", schema=schema)
    col = client.get_collection("review_docs")

# 写入：embedding 由我们自己的 embedder 计算，随文档一起提交
col.upsert(
    ids=[...],            # 字符串 id，如 "doc_{md5_12位}"
    documents=[...],      # 块文本
    embeddings=[...],     # list[list[float]]，维度 = DIM
    metadatas=[...],      # dict：{"source": 文件名, "title": 标题, "index": 序号, "kind": "material"}
)
# 旧版本无 upsert 时用 col.add(...)，参数相同

# 查询：先给查询文本算 embedding，再查
res = col.query(query_embeddings=[query_vec], n_results=k)
# 返回 dict：{"ids": [[...]], "documents": [[...]], "metadatas": [[...]], "distances": [[...]]}
# 每个字段的第一层 list 对应每条查询，取 [0] 即本条结果
```

## 四、功能实现

1. `POST /api/documents`：接收 `{"filename": str, "content": str}`。
   - 按 Markdown 标题（# / ##）+ 段落切块，每块 ≤ 800 字，块记录 `{"source": filename, "title": 最近标题, "index": 块序号}`。
   - 逐块 embed 后 upsert 进集合 `review_docs`。重复导入同一 filename 时先按 `source` 元数据删旧再写。
   - 返回 `{"chunks": N, "collection": "review_docs"}`。
2. `GET /api/documents`：列出已导入文件（从元数据聚合：文件名、块数、导入时间）。
3. `POST /api/ask`：三路混合检索。
   - **向量检索**：查询文本 embed 后 `col.query(..., n_results=12)`。
   - **关键词过滤**：对查询做简单分词（去停用词），命中关键词多的结果加分（RRF 或加权融合均可，写清楚公式）。
   - **结构化过滤**：请求可带 `{"source": "xxx.md"}` 过滤条件，优先用 metadatas 匹配在代码内过滤（若 pyseekdb 版本支持 where 参数也可用）。
   - 返回 Top-5：`{"chunks": [{"text", "source", "title", "score", "signals": {"vector": x, "keyword": y}}]}`。
4. **检索日志（重要，用于提交证据）**：每次 /api/ask 追加一行 JSON 到 `qoder/app/data/retrieval_log.jsonl`，记录时间、查询、向量/关键词得分、最终 Top-5 的 source 与 distance。
5. `GET /api/ask` 响应中的每个块都必须带出处（source + title），前端问答页把 Top-5 显示为「答案依据」列表。

## 五、自测

1. 导入仓库内任一 Markdown（如 `qoder/PRD.md`）。
2. 提问「seekdb 和 PowerContext 的分工是什么」，确认返回的块确实来自 PRD 第 5 节且带出处。
3. 查看 `retrieval_log.jsonl` 有一行记录。
4. 在另一次提问里带上 `{"source": "PRD.md"}` 过滤，确认结果全部来自该文件。

完成后输出：改动文件清单、自测结果、retrieval_log.jsonl 样例一行。

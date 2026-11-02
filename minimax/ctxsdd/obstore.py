"""seekdb（嵌入式 pyseekdb）存储与检索：规格、代码、测试的向量索引。

数据库保存工程资料索引，run-task 时实际查询结果直接用于任务上下文。
"""

import hashlib
import math
import os
import re
from pathlib import Path

DIM = 384

COLLECTIONS = ("ctxsdd_specs", "ctxsdd_code", "ctxsdd_tests")


class Embedder:
    """按名称加载嵌入函数：default(chroma)/st(sentence-transformers)/hash。"""

    def __init__(self, name: str):
        self.name = name
        self._fn = None
        if name == "default":
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

            self._fn = DefaultEmbeddingFunction()
        elif name == "st":
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            from sentence_transformers import SentenceTransformer

            self._fn = SentenceTransformer("all-MiniLM-L6-v2")
        elif name == "hash":
            pass
        else:
            raise ValueError(f"未知嵌入器: {name}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._fn is None:
            return _hash_embed(texts)
        if hasattr(self._fn, "encode"):
            raw = self._fn.encode(texts)
        else:
            raw = self._fn(texts)
        return [
            [float(x) for x in (e.tolist() if hasattr(e, "tolist") else e)]
            for e in raw
        ]


def detect_embedder() -> str:
    """探测可用的嵌入器；CTXSDD_EMBEDDER 可强制指定 default/st/hash。

    st 探测用离线模式（HF_HUB_OFFLINE=1）：模型已在本地缓存时直接可用，
    无缓存或无网络时快速失败并回退到 hash，避免在线探测长时间重试。
    """
    forced = os.environ.get("CTXSDD_EMBEDDER")
    if forced:
        return forced
    try:
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

        f = DefaultEmbeddingFunction()
        f(["ping"])
        return "default"
    except Exception:
        pass
    try:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        from sentence_transformers import SentenceTransformer

        m = SentenceTransformer("all-MiniLM-L6-v2")
        m.encode(["ping"])
        return "st"
    except Exception:
        pass
    return "hash"


def _hash_embed(texts: list[str]) -> list[list[float]]:
    """确定性哈希词袋嵌入（回退方案，384 维，无需模型下载）。"""
    out = []
    for t in texts:
        v = [0.0] * DIM
        for tok in re.findall(r"[\w\u4e00-\u9fff]+", t.lower()):
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            bucket = h % DIM
            sign = 1.0 if (h >> 64) & 1 else -1.0
            v[bucket] += sign
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / norm for x in v])
    return out


class ObStore:
    """pyseekdb 嵌入式客户端封装。"""

    def __init__(self, db_path: Path, embedder_name: str):
        import pyseekdb
        from pyseekdb import HNSWConfiguration, Schema

        self.embedder = Embedder(embedder_name)
        self.client = pyseekdb.Client(path=str(db_path))
        schema = Schema(
            vector_index=HNSWConfiguration(dimension=DIM, distance="cosine")
        )
        self.collections = {}
        for name in COLLECTIONS:
            try:
                self.collections[name] = self.client.get_collection(name)
            except Exception:
                self.client.create_collection(name=name, schema=schema)
                self.collections[name] = self.client.get_collection(name)

    # ---- 写入 ----

    def index_specs(self, chunks) -> int:
        col = self.collections["ctxsdd_specs"]
        ids = [c.chunk_id for c in chunks]
        docs = [c.text for c in chunks]
        metas = [
            {
                "source": c.source,
                "version": int(c.version),
                "heading": c.heading,
                "index": c.index,
            }
            for c in chunks
        ]
        self._write(col, ids, docs, metas)
        return len(ids)

    def index_files(self, kind: str, files: list[tuple[str, str]]) -> int:
        """kind: code | test；files: [(相对路径, 文本)]"""
        col = self.collections["ctxsdd_code" if kind == "code" else "ctxsdd_tests"]
        ids, docs, metas = [], [], []
        for rel, text in files:
            digest = hashlib.md5(rel.encode("utf-8")).hexdigest()[:12]
            ids.append(f"{kind}_{digest}")
            docs.append(text)
            metas.append({"path": rel, "kind": kind})
        if not ids:
            return 0
        self._write(col, ids, docs, metas)
        return len(ids)

    def _write(self, col, ids, docs, metas) -> None:
        embs = self.embedder.embed(docs)
        if hasattr(col, "upsert"):
            col.upsert(ids=ids, documents=docs, embeddings=embs, metadatas=metas)
        else:
            col.add(ids=ids, documents=docs, embeddings=embs, metadatas=metas)

    # ---- 查询 ----

    def query(self, collection: str, text: str, n: int = 6) -> list[dict]:
        """返回 [{id, document, metadata, distance}]，调用方再做过滤。"""
        col = self.collections[collection]
        count = 0
        if hasattr(col, "count"):
            try:
                count = col.count()
            except Exception:
                count = 0
        if not count:
            return []
        emb = self.embedder.embed([text])[0]
        res = col.query(query_embeddings=[emb], n_results=max(1, min(n, count)))
        hits = []
        ids = res.get("ids", [[]])[0] if isinstance(res, dict) else res["ids"][0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, cid in enumerate(ids):
            hits.append(
                {
                    "id": cid,
                    "document": docs[i] if i < len(docs) else "",
                    "metadata": metas[i] if i < len(metas) else {},
                    "distance": dists[i] if i < len(dists) else None,
                }
            )
        return hits

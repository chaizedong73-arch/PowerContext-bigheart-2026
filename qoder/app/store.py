"""seekdb（嵌入式 pyseekdb）封装：资料切块、写入、三路混合检索、列表与删除。

pyseekdb API 用法参照仓库 minimax/ctxsdd/obstore.py 与实测签名：
  Client(path=) / Schema(vector_index=HNSWConfiguration(dimension, distance="cosine"))
  col.upsert(ids, embeddings, metadatas, documents) / col.add(...)
  col.query(query_embeddings=[vec], n_results=k, where={...})
  col.delete(where={"source": ...}) / col.get(include=[...]) / col.count()
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import config
import embedder

MAX_CHUNK = 800

# 简单停用词（中英），用于关键词打分
_STOP = set(
    """的 了 和 是 在 我 有 也 就 不 人 都 一 上 与 及 或 等 被 把 对 为 以 而 及 之 其 此 该 中 什么 如何 怎么 哪些 一个 可以 这个 那个 我们 你们 他们
a an the is are was were be been of to in on for and or not with as at by from it this that these those what how why which""".split()
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_WORD_RE = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]+")

_lock = threading.RLock()  # 嵌入式 seekdb 客户端非线程安全：所有集合操作必须串行
_client = None
_collection = None


# ---------------- 切块 ----------------


def _split_paragraphs(text: str) -> List[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text)]
    return [p for p in parts if p]


def _hard_split(para: str, size: int = MAX_CHUNK) -> List[str]:
    if len(para) <= size:
        return [para]
    out, cur = [], ""
    for line in para.split("\n"):
        if len(cur) + len(line) + 1 > size and cur:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    # 仍超长的按字符切
    final = []
    for piece in out:
        while len(piece) > size:
            final.append(piece[:size])
            piece = piece[size:]
        if piece:
            final.append(piece)
    return final


def chunk_markdown(source: str, content: str) -> List[Dict]:
    """按 Markdown 标题 + 段落切块，每块 ≤ 800 字，记录 source/title/index。"""
    lines = content.split("\n")
    sections: List[Dict] = []  # {title, body_lines}
    cur_title = source
    cur_body: List[str] = []
    for ln in lines:
        m = _HEADING_RE.match(ln.strip())
        if m:
            if cur_body:
                sections.append({"title": cur_title, "body": "\n".join(cur_body)})
            cur_title = m.group(2).strip() or cur_title
            cur_body = [ln]
        else:
            cur_body.append(ln)
    if cur_body:
        sections.append({"title": cur_title, "body": "\n".join(cur_body)})

    chunks: List[Dict] = []
    idx = 0
    for sec in sections:
        title = sec["title"]
        body = sec["body"].strip()
        if not body:
            continue
        buf = ""
        for para in _split_paragraphs(body):
            for piece in _hard_split(para):
                if len(buf) + len(piece) + 2 <= MAX_CHUNK:
                    buf = f"{buf}\n\n{piece}" if buf else piece
                else:
                    if buf:
                        chunks.append(_mk_chunk(source, title, idx, buf))
                        idx += 1
                    buf = piece
        if buf:
            chunks.append(_mk_chunk(source, title, idx, buf))
            idx += 1
    return chunks


def _mk_chunk(source: str, title: str, index: int, text: str) -> Dict:
    digest = hashlib.md5(f"{source}:{index}:{text}".encode("utf-8")).hexdigest()[:12]
    return {
        "id": f"doc_{digest}",
        "text": text,
        "metadata": {
            "source": source,
            "title": title,
            "index": index,
            "kind": "material",
            "imported_at": int(time.time()),
        },
    }


# ---------------- seekdb 客户端 ----------------


def _get_collection():
    global _client, _collection
    if _collection is not None:
        return _collection
    with _lock:
        if _collection is not None:
            return _collection
        import pyseekdb
        from pyseekdb import HNSWConfiguration, Schema

        config.ensure_dirs()
        db_path = Path(config.SEEKDB_PATH)
        db_path.mkdir(parents=True, exist_ok=True)
        _client = pyseekdb.Client(path=str(db_path))
        schema = Schema(
            vector_index=HNSWConfiguration(dimension=config.EMBEDDING_DIM, distance="cosine")
        )
        try:
            _collection = _client.get_collection(config.COLLECTION_NAME)
        except Exception:
            _client.create_collection(name=config.COLLECTION_NAME, schema=schema)
            _collection = _client.get_collection(config.COLLECTION_NAME)
    return _collection


def _write(col, ids, docs, embs, metas) -> None:
    if hasattr(col, "upsert"):
        col.upsert(ids=ids, documents=docs, embeddings=embs, metadatas=metas)
    else:
        col.add(ids=ids, documents=docs, embeddings=embs, metadatas=metas)


# ---------------- 业务操作 ----------------


def import_document(filename: str, content: str) -> int:
    chunks = chunk_markdown(filename, content)
    if not chunks:
        return 0
    ids = [c["id"] for c in chunks]
    docs = [c["text"] for c in chunks]
    metas = [c["metadata"] for c in chunks]
    embs = embedder.embed(docs)
    with _lock:
        col = _get_collection()
        # 重复导入同一 filename：先按 source 元数据删旧
        try:
            col.delete(where={"source": filename})
        except Exception:
            pass
        _write(col, ids, docs, embs, metas)
        try:
            if hasattr(col, "refresh_index"):
                col.refresh_index()
        except Exception:
            pass
    return len(chunks)


def delete_document(filename: str) -> bool:
    with _lock:
        col = _get_collection()
        try:
            col.delete(where={"source": filename})
            return True
        except Exception:
            return False


def list_documents() -> List[Dict]:
    with _lock:
        col = _get_collection()
        try:
            count = col.count()
        except Exception:
            count = 0
        if not count:
            return []
        try:
            res = col.get(limit=count, include=["metadatas"])
        except Exception:
            res = col.get(include=["metadatas"])
    metas = (res or {}).get("metadatas") or []
    agg: Dict[str, Dict] = {}
    for m in metas:
        if not m:
            continue
        src = m.get("source", "?")
        a = agg.setdefault(src, {"filename": src, "chunks": 0, "imported_at": 0})
        a["chunks"] += 1
        a["imported_at"] = max(a["imported_at"], int(m.get("imported_at", 0) or 0))
    out = sorted(agg.values(), key=lambda x: x["filename"])
    return out


def fetch_chunks(limit: int = 500) -> List[Dict]:
    """取出集合内的资料块（用于出题）：[{id, text, source, title, index}]。"""
    with _lock:
        col = _get_collection()
        try:
            count = col.count()
        except Exception:
            count = 0
        if not count:
            return []
        try:
            res = col.get(limit=min(limit, count), include=["documents", "metadatas"])
        except Exception:
            res = col.get(include=["documents", "metadatas"])
    ids = (res or {}).get("ids") or []
    docs = (res or {}).get("documents") or []
    metas = (res or {}).get("metadatas") or []
    out = []
    for i, cid in enumerate(ids):
        m = metas[i] if i < len(metas) else {}
        out.append(
            {
                "id": cid,
                "text": docs[i] if i < len(docs) else "",
                "source": m.get("source", ""),
                "title": m.get("title", ""),
                "index": m.get("index", i),
            }
        )
    out.sort(key=lambda c: (c["source"], c["index"]))
    return out


def _keywords(text: str) -> List[str]:
    toks = [t for t in _WORD_RE.findall(text.lower()) if t not in _STOP and len(t) > 0]
    # 去重保序
    seen, out = set(), []
    for t in toks:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _keyword_score(query_kws: List[str], doc_text: str) -> float:
    if not query_kws:
        return 0.0
    low = doc_text.lower()
    hit = sum(1 for k in query_kws if k in low)
    return hit / len(query_kws)


def hybrid_search(question: str, source: Optional[str] = None, top_k: Optional[int] = None):
    """三路混合检索：向量相似 + 关键词匹配 + 结构化(source)过滤。

    融合公式（加权，KEYWORD_WEIGHT 默认 0.4）：
      vector_sim = 1 - cosine_distance
      keyword    = 命中查询关键词数 / 查询关键词总数
      score      = (1 - w) * vector_sim + w * keyword
    返回 (results, log_record)。
    """
    col = _get_collection()
    k = top_k or config.ASK_TOP_K
    qvec = embedder.embed([question])[0]
    with _lock:
        try:
            count = col.count()
        except Exception:
            count = 0
        if not count:
            return [], {"query": question, "source": source, "candidates": 0, "results": []}
        n_cand = max(k, min(config.VECTOR_CANDIDATES, count))
        where = {"source": source} if source else None
        try:
            res = col.query(query_embeddings=[qvec], n_results=n_cand, where=where) if where else \
                  col.query(query_embeddings=[qvec], n_results=n_cand)
        except Exception:
            # where 不支持时退回全量查询 + 代码侧过滤
            res = col.query(query_embeddings=[qvec], n_results=n_cand)

    ids = (res.get("ids") or [[]])[0]
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]

    qkws = _keywords(question)
    w = config.KEYWORD_WEIGHT
    scored = []
    for i, cid in enumerate(ids):
        text = docs[i] if i < len(docs) else ""
        meta = metas[i] if i < len(metas) else {}
        dist = dists[i] if i < len(dists) else None
        # 代码侧结构化过滤兜底
        if source and meta.get("source") != source:
            continue
        vec_sim = (1.0 - float(dist)) if dist is not None else 0.0
        kw = _keyword_score(qkws, text)
        score = (1 - w) * vec_sim + w * kw
        scored.append(
            {
                "id": cid,
                "text": text,
                "source": meta.get("source", ""),
                "title": meta.get("title", ""),
                "score": round(score, 4),
                "signals": {"vector": round(vec_sim, 4), "keyword": round(kw, 4)},
                "distance": round(float(dist), 4) if dist is not None else None,
            }
        )
    scored.sort(key=lambda x: x["score"], reverse=True)
    results = scored[:k]
    log_record = {
        "query": question,
        "source": source,
        "candidates": len(scored),
        "results": [
            {"source": r["source"], "title": r["title"], "score": r["score"],
             "vector": r["signals"]["vector"], "keyword": r["signals"]["keyword"],
             "distance": r["distance"]}
            for r in results
        ],
    }
    return results, log_record

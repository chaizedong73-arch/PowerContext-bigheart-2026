"""seekdb 档案库封装：建集合（HNSW 向量 + ik 全文索引）→ 灌入原创怪谈档案 → 三路混合检索。

seekdb 以嵌入式模式运行（pyseekdb.Client(path=...)，无 MySQL 端口），因此检索全部通过
pyseekdb SDK 完成；三条能力各自留痕，便于验证「向量 / 全文 / 结构化过滤确实都在起作用」：

  * 结构化：``where`` 元数据过滤（kind / scene_id / danger / sealed / difficulty）
  * 全文：  ``where_document`` 的 ``$contains`` 命中 ik 分词全文索引
  * 向量：  ``query_embeddings`` 走 HNSW cosine ANN
  * 融合：  ``Collection.hybrid_search(..., rank={"rrf": ...})`` 由 seekdb 侧做 RRF 融合
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional

import embedder

DB_PATH = Path(os.environ.get("ARCHIVE_SEEKDB_PATH", str(Path.home() / "dify-archive-data" / "seekdb")))
COLLECTION_NAME = os.environ.get("ARCHIVE_COLLECTION", "ghost_archive")
DATA_FILE = Path(os.environ.get("ARCHIVE_DATA_FILE", str(Path(__file__).resolve().parent / "data" / "scenes.json")))

_lock = threading.RLock()
_client = None
_collection = None

# 最近调用留痕（供 /archive/evidence 输出，作为「检索结果如何进入互动」的验证材料）
_CALL_LOG: deque = deque(maxlen=60)


# --------------------------------------------------------------------------- io


def _schema():
    from pyseekdb import FulltextIndexConfig, HNSWConfiguration, Schema

    return Schema(
        vector_index=HNSWConfiguration(dimension=embedder.DIM, distance="cosine"),
        fulltext_index=FulltextIndexConfig(analyzer="ik"),
    )


def collection(recreate: bool = False):
    global _client, _collection
    if _collection is not None and not recreate:
        return _collection
    with _lock:
        import pyseekdb

        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        if _client is None:
            _client = pyseekdb.Client(path=str(DB_PATH))
        if recreate and _client.has_collection(COLLECTION_NAME):
            _client.delete_collection(COLLECTION_NAME)
        _collection = _client.get_or_create_collection(COLLECTION_NAME, schema=_schema())
        return _collection


def _is_dead_connection(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "gone away" in text or "2006" in text


def _retry_dead(fn):
    """在锁内跑一次；撞上「连接已死」就丢掉缓存重开再跑一次。

    嵌入式 seekdb 只有一条连接，而 FastAPI 把 sync 端点丢进线程池：跨线程共用同一条
    连接实测会打出 ``SeekdbError: Server has gone away failed: code=2006``（跑了一两个
    小时后出现，一次连挂四个云端回合）。缓存里的 collection 指向的仍是那条死连接，
    后果分两种，都很难看：写路径直接 500 中断整轮互动；读路径更糟——``hybrid_search``
    三条通道各自 ``except`` 把死连接吞成「本轮没有命中」，HTTP 200、hits 全空，玩家看到的
    是「档案室今晚查不到东西」。所以两头一起修：**取数全程持锁串行**（根因），
    **命中死连接就重建集合重试一次**（兜底）。
    """
    global _collection
    with _lock:
        try:
            return fn(collection())
        except Exception as exc:
            if not _is_dead_connection(exc):
                raise
            _collection = None
            return fn(collection())


# ----------------------------------------------------------------------- import


def _doc_text(entry: Dict[str, Any]) -> str:
    tags = entry.get("tags") or ""
    return f"[{entry['code']}] {entry['title']}｜{entry['text']}｜关键词：{tags}".strip("｜")


def _meta(entry: Dict[str, Any], scene: Dict[str, Any], kind: str) -> Dict[str, Any]:
    """pyseekdb metadata 只接受标量，统一转换。"""
    return {
        "kind": kind,
        "scene_id": scene["scene_id"],
        "scene_title": scene["title"],
        "code": entry["code"],
        "title": entry["title"],
        "danger": int(entry.get("danger") or scene.get("danger") or 1),
        "difficulty": int(scene.get("difficulty") or 1),
        "sealed": bool(entry.get("sealed", False)),
        "tags": str(entry.get("tags") or ""),
    }


def scene_doc_records(scene: Dict[str, Any]) -> List[Dict[str, Any]]:
    """场景本体也入库，供「按口味描述匹配场景」的向量检索使用。"""
    doc = f"[{scene['scene_id']}] {scene['title']}｜{scene['one_line']}｜{scene['brief']}｜主持风格：{scene['tone']}"
    return [
        {
            "id": f"{scene['scene_id']}:scene:{scene['scene_id']}",
            "document": doc,
            "metadata": {
                "kind": "scene",
                "scene_id": scene["scene_id"],
                "scene_title": scene["title"],
                "code": scene["scene_id"],
                "title": scene["title"],
                "danger": int(scene.get("danger") or 1),
                "difficulty": int(scene.get("difficulty") or 1),
                "sealed": False,
                "tags": str(scene.get("tags") or ""),
            },
        }
    ]


def load_corpus(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    raw = json.loads((path or DATA_FILE).read_text(encoding="utf-8"))
    records: List[Dict[str, Any]] = []
    for scene in raw["scenes"]:
        records.extend(scene_doc_records(scene))
        for kind, entries in (scene.get("entries") or {}).items():
            for entry in entries:
                records.append(
                    {
                        "id": f"{scene['scene_id']}:{kind}:{entry['code']}",
                        "document": _doc_text(entry),
                        "metadata": _meta(entry, scene, kind),
                    }
                )
    return records


def seed(recreate: bool = True) -> Dict[str, Any]:
    """把原创档案批量写入 seekdb（embed 后 upsert）。"""
    t0 = time.time()
    records = load_corpus()
    col = collection(recreate=recreate)
    batch = 40
    for i in range(0, len(records), batch):
        chunk = records[i : i + batch]
        col.upsert(
            ids=[r["id"] for r in chunk],
            documents=[r["document"] for r in chunk],
            embeddings=embedder.embed([r["document"] for r in chunk]),
            metadatas=[r["metadata"] for r in chunk],
        )
    out = {
        "ok": True,
        "engine": "seekdb(embedded)",
        "dbPath": str(DB_PATH),
        "collection": COLLECTION_NAME,
        "records": len(records),
        "countAfter": int(col.count()),
        "embeddingProvider": embedder.provider(),
        "tookMs": int((time.time() - t0) * 1000),
    }
    _record_call("seed", {"recreate": recreate}, out)
    return out


# -------------------------------------------------------------------- retrieval


def _norm_rows(res: Dict[str, Any]) -> List[Dict[str, Any]]:
    """统一 pyseekdb 两种返回形状：query/hybrid_search 按查询分组（ids[0]…），
    get() 返回扁平列表（ids…）。"""

    def first(key: str) -> List[Any]:
        val = res.get(key) or []
        if val and isinstance(val[0], list):
            return val[0]
        return val

    ids = first("ids")
    docs = first("documents")
    metas = first("metadatas")
    dist = first("distances")
    rows = []
    for i, rid in enumerate(ids):
        meta = metas[i] if i < len(metas) else {}
        rows.append(
            {
                "entry_id": rid,
                "code": meta.get("code"),
                "kind": meta.get("kind"),
                "scene_id": meta.get("scene_id"),
                "title": meta.get("title"),
                "danger": meta.get("danger"),
                "sealed": meta.get("sealed"),
                "document": docs[i] if i < len(docs) else "",
                "distance": dist[i] if i < len(dist) else None,
                "score": round(1.0 - float(dist[i]), 4) if i < len(dist) and dist[i] is not None else None,
            }
        )
    return rows


def _where(
    kinds: Optional[List[str]] = None,
    scene_id: Optional[str] = None,
    danger_min: Optional[int] = None,
    difficulty_max: Optional[int] = None,
    include_sealed: bool = True,
) -> Dict[str, Any]:
    where: Dict[str, Any] = {}
    if kinds:
        where["kind"] = {"$in": list(kinds)} if len(kinds) > 1 else {"$eq": kinds[0]}
    if scene_id:
        where["scene_id"] = {"$eq": scene_id}
    if danger_min:
        where["danger"] = {"$gte": int(danger_min)}
    if difficulty_max:
        where["difficulty"] = {"$lte": int(difficulty_max)}
    if not include_sealed:
        where["sealed"] = {"$eq": False}
    return where


def hybrid_search(
    query: str,
    *,
    keywords: Optional[List[str]] = None,
    kinds: Optional[List[str]] = None,
    scene_id: Optional[str] = None,
    danger_min: Optional[int] = None,
    difficulty_max: Optional[int] = None,
    include_sealed: bool = True,
    n_results: int = 8,
) -> Dict[str, Any]:
    """一次调用里同时留下三条通道的证据：fused(RRF) / vector-only / fulltext-only。"""
    t0 = time.time()
    where = _where(kinds, scene_id, danger_min, difficulty_max, include_sealed)
    kw = [k for k in (keywords or []) if k]
    if not kw:
        kw = _auto_keywords(query)
    contains = {"$contains": (kw[0] if kw else query)}
    ft_clause = {"where_document": contains, "n_results": n_results}
    if where:
        ft_clause["where"] = where
    knn_clause = {"query_embeddings": [embedder.embed([query])[0]], "n_results": n_results}
    if where:
        knn_clause["where"] = where

    channels: Dict[str, Any] = {}
    try:
        fused_res = _retry_dead(lambda c: c.hybrid_search(query=ft_clause, knn=knn_clause, rank={"rrf": {"rank_window_size": 30, "rank_constant": 20}}, n_results=n_results, include=["documents", "metadatas"]))
        fused = _norm_rows(fused_res)
        channels["fused"] = {"engine": "seekdb.hybrid_search(rank=rrf)", "hits": [r["code"] for r in fused]}
    except Exception as exc:  # 融合不可用时退化为向量通道，并把原因留痕
        fused = []
        channels["fused"] = {"engine": "seekdb.hybrid_search(rank=rrf)", "error": f"{type(exc).__name__}: {exc}"[:200]}

    try:
        vec_res = _retry_dead(lambda c: c.query(query_embeddings=embedder.embed([query]), n_results=n_results, where=where or None, include=["documents", "metadatas"]))
        vec = _norm_rows(vec_res)
        channels["vector"] = {"engine": "seekdb.query(HNSW cosine)", "hits": [r["code"] for r in vec]}
    except Exception as exc:
        vec = []
        channels["vector"] = {"engine": "seekdb.query(HNSW cosine)", "error": f"{type(exc).__name__}: {exc}"[:200]}

    try:
        ft_res = _retry_dead(lambda c: c.get(where_document=contains, where=where or None, limit=n_results, include=["documents", "metadatas"]))
        ft = _norm_rows(ft_res)
        channels["fulltext"] = {"engine": "seekdb.get(where_document=$contains)", "keyword": (kw[0] if kw else query), "hits": [r["code"] for r in ft]}
    except Exception as exc:
        ft = []
        channels["fulltext"] = {"engine": "seekdb.get(where_document=$contains)", "error": f"{type(exc).__name__}: {exc}"[:200]}

    if not fused:
        # 兜底顺序：向量 → 全文 → 结构化列表，保证互动不中断
        fused = vec or ft

    seen, ranked = set(), []
    for rank_no, row in enumerate(fused):
        if row["code"] in seen:
            continue
        seen.add(row["code"])
        row["rank"] = rank_no + 1
        row["matched_by"] = sorted({ch for ch, res in (("vector", vec), ("fulltext", ft), ("fused", fused)) if any(v["code"] == row["code"] for v in res)})
        ranked.append(row)

    out = {
        "ok": True,
        "query": query,
        "filters": where,
        "keywords": kw,
        "embeddingProvider": embedder.provider(),
        "channels": channels,
        "hits": ranked,
        "tookMs": int((time.time() - t0) * 1000),
    }
    _record_call("search", {"query": query, "filters": where, "keywords": kw}, {"hits": [r["code"] for r in ranked], "tookMs": out["tookMs"]})
    return out


def _auto_keywords(text: str) -> List[str]:
    """兜底关键词抽取：仅在调用方未显式传 keywords 时使用。

    Dify 侧的关键词抽取节点会给出更好的 2-4 字怪谈名词；这里按「短片段优先」切，
    因为中文全文索引对过长片段（如整句前 6 字）命中率很差。
    """
    import re

    runs = re.findall(r"[\u4e00-\u9fff]{2,4}", text)
    stop = {"一个", "我们", "他们", "这里", "然后", "现在", "什么", "可以", "自己", "已经", "因为", "所以", "但是", "我想", "我要"}
    picked: List[str] = []
    for run in runs:
        if run in stop or run in picked:
            continue
        picked.append(run)
        if len(picked) >= 3:
            break
    return picked


def list_by_filter(kinds: Optional[List[str]] = None, scene_id: Optional[str] = None, include_sealed: bool = True, limit: int = 60) -> List[Dict[str, Any]]:
    """纯结构化查询（不走相似度）：取回某场景全部生效规则/道具等。"""
    where = _where(kinds, scene_id, None, None, include_sealed)
    res = _retry_dead(lambda c: c.get(where=where or None, limit=limit, include=["documents", "metadatas"]))
    return _norm_rows(res)


def stats() -> Dict[str, Any]:
    count = _retry_dead(lambda c: int(c.count()))
    return {
        "engine": "seekdb(embedded)",
        "dbPath": str(DB_PATH),
        "collection": COLLECTION_NAME,
        "dimension": embedder.DIM,
        "indexes": {"vector": "HNSW/cosine", "fulltext": "ik"},
        "count": count,
        "embeddingProvider": embedder.provider(),
    }


def _record_call(op: str, req: Dict[str, Any], resp: Dict[str, Any]) -> None:
    _CALL_LOG.append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "op": op, "request": req, "response": resp})


def recent_calls(limit: int = 20) -> List[Dict[str, Any]]:
    items = list(_CALL_LOG)
    return items[-limit:][::-1]


def corpus_scenes() -> List[Dict[str, Any]]:
    """场景清单同样从库里聚合，不回落 data/scenes.json。"""
    res = _retry_dead(lambda c: c.get(where=_where(kinds=["scene"]), limit=60, include=["documents", "metadatas"]))
    counts = _rule_counts()
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for i, row in enumerate(_norm_rows(res)):
        sid = row["scene_id"]
        if not sid or sid in seen:
            continue
        seen.add(sid)
        meta = _meta_of(res, i)
        parsed = _parse_scene_document(row["document"], sid)
        out.append(
            {
                "scene_id": sid,
                "title": parsed.get("title") or meta.get("title") or sid,
                "one_line": parsed.get("one_line", ""),
                "difficulty": meta.get("difficulty"),
                "danger": meta.get("danger"),
                "tags": meta.get("tags"),
                "rule_count": counts.get(sid, 0),
            }
        )
    out.sort(key=lambda s: (s["difficulty"] is None, s["difficulty"], s["scene_id"]))
    return out


def scene_brief(scene_id: str) -> Dict[str, Any]:
    """运行期取场景正文：只读 seekdb 里那条 kind=scene 的记录。

    ``data/scenes.json`` 仅在 seed 时被读取；玩家拿到的 brief/tone 全部来自库，
    所以「改了文件没重灌 seed」不会反映到运行时，反之库就是唯一事实来源。
    """
    col = collection()
    res = col.get(where=_where(kinds=["scene"], scene_id=scene_id), limit=1, include=["documents", "metadatas"])
    rows = _norm_rows(res)
    if not rows:
        return {}
    meta = _meta_of(res, 0)
    scene = {
        "scene_id": scene_id,
        "difficulty": meta.get("difficulty"),
        "danger": meta.get("danger"),
        "tags": meta.get("tags"),
    }
    scene.update(_parse_scene_document(rows[0]["document"], scene_id))
    return scene


def _rule_counts() -> Dict[str, int]:
    """按场景统计生效规则条数，同样只问库。"""
    col = collection()
    res = col.get(where=_where(kinds=["rule"]), limit=200, include=["metadatas"])
    counts: Dict[str, int] = {}
    for row in _norm_rows(res):
        if row["scene_id"]:
            counts[row["scene_id"]] = counts.get(row["scene_id"], 0) + 1
    return counts


def _meta_of(res: Dict[str, Any], index: int) -> Dict[str, Any]:
    metas = res.get("metadatas") or []
    if metas and isinstance(metas[0], list):
        metas = metas[0]
    return metas[index] if index < len(metas) and metas[index] else {}


def _parse_scene_document(document: str, scene_id: str) -> Dict[str, Any]:
    """按 seed 时的拼接格式反解：``[scene_id] title｜one_line｜brief｜主持风格：tone``。"""
    head = f"[{scene_id}] "
    body = document[len(head) :] if document.startswith(head) else document
    parts = body.split("｜")
    if len(parts) != 4:
        return {}
    tone = parts[3]
    prefix = "主持风格："
    return {
        "title": parts[0],
        "one_line": parts[1],
        "brief": parts[2],
        "tone": tone[len(prefix) :] if tone.startswith(prefix) else tone,
    }

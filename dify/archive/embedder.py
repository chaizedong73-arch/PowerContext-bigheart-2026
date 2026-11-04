"""Embedding 层：优先本地 sentence-transformers，失败则降级为确定性哈希向量。

两条路径都输出 384 维、L2 归一化的向量，因此 seekdb 集合的 HNSW 维度不需要随
provider 改变；哈希路径保证离线/无依赖环境也能跑通完整玩法（语义精度较低，
但全文检索通道仍提供关键词命中）。
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import threading
from typing import List

DIM = int(os.environ.get("ARCHIVE_EMBEDDING_DIM", "384"))
MODEL_NAME = os.environ.get("ARCHIVE_EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")

_TOKEN_RE = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")

_lock = threading.Lock()
_model = None
_model_failed = False
_last_provider = "unknown"


def _normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def _hash_one(text: str) -> List[float]:
    v = [0.0] * DIM
    low = text.lower()
    feats: List[str] = _TOKEN_RE.findall(low)
    for run in _CJK_RE.findall(low):
        feats.extend(run[i : i + 2] for i in range(len(run) - 1))
    for f in feats:
        h = int(hashlib.md5(f.encode("utf-8")).hexdigest(), 16)
        v[h % DIM] += 1.0 if (h >> 64) & 1 else -1.0
    return _normalize(v)


def _get_model():
    global _model, _model_failed
    if _model is not None or _model_failed:
        return _model
    with _lock:
        if _model is None and not _model_failed:
            # 默认离线：模型需预先下载到 HuggingFace 缓存，避免无网环境下反复重试拖慢启动。
            if os.environ.get("ARCHIVE_EMBEDDING_ALLOW_NET") != "1":
                os.environ.setdefault("HF_HUB_OFFLINE", "1")
                os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            try:
                from sentence_transformers import SentenceTransformer

                _model = SentenceTransformer(MODEL_NAME)
            except Exception:
                _model_failed = True
                _model = None
    return _model


def embed(texts: List[str]) -> List[List[float]]:
    """Return one normalized vector per input text."""
    global _last_provider
    if not texts:
        return []
    model = _get_model()
    if model is not None:
        vecs = [[float(x) for x in row] for row in model.encode(texts, normalize_embeddings=False)]
        _last_provider = f"sentence-transformers:{MODEL_NAME}"
        return [_resize_and_norm(v) for v in vecs]
    _last_provider = "deterministic-hash"
    return [_hash_one(t) for t in texts]


def _resize_and_norm(vec: List[float]) -> List[float]:
    if len(vec) > DIM:
        vec = vec[:DIM]
    elif len(vec) < DIM:
        vec = vec + [0.0] * (DIM - len(vec))
    return _normalize(vec)


def provider() -> str:
    return _last_provider

"""Embedding 抽象：local（无 Key 可跑的确定性哈希）与 api（OpenAI 兼容，可选）。

统一接口 embed(texts) -> list[list[float]]，维度 = config.EMBEDDING_DIM。
local 实现：分词 + 中文 2-gram 特征，md5 映射到固定维度并 L2 归一化，
保证同文本结果一致、语义无关文本向量接近正交。
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import List

import config

_TOKEN_RE = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def _features(text: str) -> List[str]:
    """英文按词、中文按单字 + 相邻 2-gram，兼顾中英文检索。"""
    low = text.lower()
    feats: List[str] = _TOKEN_RE.findall(low)
    for run in _CJK_RE.findall(low):
        for i in range(len(run) - 1):
            feats.append(run[i : i + 2])
    return feats


def _hash_embed_one(text: str, dim: int) -> List[float]:
    v = [0.0] * dim
    for f in _features(text):
        h = int(hashlib.md5(f.encode("utf-8")).hexdigest(), 16)
        bucket = h % dim
        sign = 1.0 if (h >> 64) & 1 else -1.0
        v[bucket] += sign
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def _local_embed(texts: List[str], dim: int) -> List[List[float]]:
    return [_hash_embed_one(t, dim) for t in texts]


def _api_embed(texts: List[str], dim: int) -> List[List[float]]:
    import httpx

    base = config.EMBEDDING_API_BASE.rstrip("/")
    url = f"{base}/embeddings" if base else "https://api.openai.com/v1/embeddings"
    headers = {"Content-Type": "application/json"}
    if config.EMBEDDING_API_KEY:
        headers["Authorization"] = f"Bearer {config.EMBEDDING_API_KEY}"
    payload = {"model": config.EMBEDDING_MODEL or "text-embedding-3-small", "input": texts}
    with httpx.Client(timeout=30) as client:
        r = client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()["data"]
    vecs = [[float(x) for x in d["embedding"]] for d in data]
    # 维度对齐：截断或补零到 dim，保证与集合维度一致
    out = []
    for v in vecs:
        if len(v) >= dim:
            v = v[:dim]
        else:
            v = v + [0.0] * (dim - len(v))
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / norm for x in v])
    return out


def embed(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []
    dim = config.EMBEDDING_DIM
    if config.EMBEDDING_PROVIDER == "api" and config.EMBEDDING_API_BASE:
        try:
            return _api_embed(texts, dim)
        except Exception:
            # 无 Key / 网络失败时回退到 local，保证演示不中断
            return _local_embed(texts, dim)
    return _local_embed(texts, dim)

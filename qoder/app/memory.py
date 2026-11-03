"""PowerContext 学习者记忆客户端（HTTP）。

端点与字段均按仓库 src/powercontext/http/_generated/schema.py 核实：
  POST /v1/scopes            {title, summary, idempotency_key}      -> {scope_id,...}
  POST /v1/sources/content   {scope_id, source_id, content, metadata}-> {status, source, position}
  POST /v1/memory/remember   {scope_id, kind, text, reason?}        -> MemoryMutationResponse
  POST /v1/memory/search     {scope_id, query, limit?}              -> {hits:[{citation,text,score,matched_by}]}
  POST /v1/context/prepare   {scope_id, query, max_bytes?}          -> {status:"ready"|"empty", content,...}

安全：Token 仅从环境变量读取，绝不写入 URL/日志；scope_id 由应用决定并持久化。
所有成功调用向 data/memory_log.jsonl 追加一行证据（不含 Token）。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import config

_SCOPE_FILE = config.DATA_DIR / "scope.json"
_MEM_LOG = config.DATA_DIR / "memory_log.jsonl"
_IDEMPOTENCY_KEY = "recallloop-app"

_cached_scope: Optional[str] = None


class PowerContextUnavailable(Exception):
    """PowerContext Server 未启动或不可达。"""


def _headers() -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if config.POWERCONTEXT_TOKEN:
        h["Authorization"] = f"Bearer {config.POWERCONTEXT_TOKEN}"
    return h


def _log(endpoint: str, req_summary: Dict, resp_summary: Dict) -> None:
    config.ensure_dirs()
    line = {
        "ts": int(time.time()),
        "endpoint": endpoint,
        "request": req_summary,
        "response": resp_summary,
    }
    with open(_MEM_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def _post(endpoint: str, payload: Dict) -> Dict:
    import httpx

    url = f"{config.POWERCONTEXT_URL}{endpoint}"
    try:
        with httpx.Client(timeout=config.POWERCONTEXT_TIMEOUT) as client:
            r = client.post(url, json=payload, headers=_headers())
    except Exception as e:  # 连接失败
        raise PowerContextUnavailable(f"无法连接 PowerContext（{url}）：{e}") from e
    if r.status_code >= 400:
        raise PowerContextUnavailable(f"{endpoint} 返回 {r.status_code}：{r.text[:200]}")
    try:
        return r.json()
    except Exception:
        return {}


# ---------------- Scope ----------------


def ensure_scope() -> str:
    """返回稳定 scope_id：环境变量 > data/scope.json 缓存 > 调 /v1/scopes 创建。"""
    global _cached_scope
    if config.POWERCONTEXT_SCOPE_ID:
        _cached_scope = config.POWERCONTEXT_SCOPE_ID
        return _cached_scope
    if _cached_scope:
        return _cached_scope
    config.ensure_dirs()
    if _SCOPE_FILE.exists():
        try:
            _cached_scope = json.loads(_SCOPE_FILE.read_text(encoding="utf-8")).get("scope_id")
            if _cached_scope:
                return _cached_scope
        except Exception:
            pass
    resp = _post(
        "/v1/scopes",
        {
            "title": "复习教练 RecallLoop",
            "summary": "个人复习教练的学习者记忆 Scope：薄弱点、复习偏好与进度。",
            "idempotency_key": _IDEMPOTENCY_KEY,
        },
    )
    scope_id = resp.get("scope_id", "")
    if not scope_id:
        raise PowerContextUnavailable(f"/v1/scopes 未返回 scope_id：{resp}")
    _SCOPE_FILE.write_text(json.dumps({"scope_id": scope_id}, ensure_ascii=False), encoding="utf-8")
    _cached_scope = scope_id
    print(f"[memory] created scope_id={scope_id}")
    _log("/v1/scopes", {"idempotency_key": _IDEMPOTENCY_KEY}, {"scope_id": scope_id})
    return scope_id


# ---------------- 写入 ----------------


def capture_source(source_id: str, content: str, metadata: Optional[Dict] = None) -> Dict:
    scope_id = ensure_scope()
    payload = {"scope_id": scope_id, "source_id": source_id, "content": content}
    if metadata:
        payload["metadata"] = metadata
    resp = _post("/v1/sources/content", payload)
    _log(
        "/v1/sources/content",
        {"scope_id": scope_id, "source_id": source_id, "content_len": len(content)},
        {"status": resp.get("status"), "position": resp.get("position")},
    )
    return resp


def remember(text: str, kind: str = "observation", reason: Optional[str] = None) -> Dict:
    scope_id = ensure_scope()
    payload = {"scope_id": scope_id, "kind": kind, "text": text}
    if reason:
        payload["reason"] = reason
    resp = _post("/v1/memory/remember", payload)
    _log(
        "/v1/memory/remember",
        {"scope_id": scope_id, "kind": kind, "text": text[:120]},
        {"status": resp.get("status") or "ok"},
    )
    return resp


def record_review(event: Dict) -> Dict:
    """一次自评结束后：留底 Source + 薄弱点写 Memory。

    event: {question, answer, result(pass|fail|fuzzy), knowledge, source, title, attempt}
    """
    result = event.get("result", "fuzzy")
    ts = int(time.time())
    # source_id 必须全局唯一：同一秒内多张卡内容不同，仅用时间戳会触发 409 source_conflict。
    source_id = f"review-{ts}-{uuid.uuid4().hex[:8]}"
    content = (
        f"问题：{event.get('question','')}\n"
        f"用户答案：{event.get('answer','')}\n"
        f"自评结果：{result}\n"
        f"知识点：{event.get('knowledge','')}\n"
        f"出处引用：{event.get('source','')} / {event.get('title','')}"
    )
    out: Dict = {"captured": False, "remembered": False}
    capture_source(source_id, content, metadata={"result": result, "source": event.get("source", "")})
    out["captured"] = True
    if result in ("fail", "fuzzy"):
        label = "不会" if result == "fail" else "模糊"
        text = (
            f"薄弱知识点：{event.get('knowledge') or event.get('question','')[:40]}，"
            f"来源 {event.get('source','')}，第 {event.get('attempt',1)} 次答错（{label}）"
        )
        remember(text, kind="observation", reason="复习自测标记为薄弱点")
        out["remembered"] = True
    return out


def save_preference(text: str) -> Dict:
    """用户偏好（每天复习时长、优先方向）用 kind=decision 写入。"""
    return remember(text, kind="decision", reason="用户设置的复习偏好")


# ---------------- 读取 ----------------


def load_context(query: str, max_bytes: int = 4000) -> Dict:
    """调 /v1/context/prepare 取回历史上下文；status==empty 时返回空，不伪造历史。"""
    scope_id = ensure_scope()
    resp = _post(
        "/v1/context/prepare",
        {"scope_id": scope_id, "query": query, "max_bytes": max_bytes},
    )
    status = resp.get("status", "empty")
    content = resp.get("content") or ""
    _log(
        "/v1/context/prepare",
        {"scope_id": scope_id, "query": query[:60], "max_bytes": max_bytes},
        {"status": status, "content_bytes": resp.get("content_bytes", len(content))},
    )
    return {"status": status, "content": content}


def search_memory(query: str, limit: int = 10) -> List[Dict]:
    """调 /v1/memory/search，返回 [{text, score}]，供出题按薄弱点过滤。"""
    scope_id = ensure_scope()
    resp = _post("/v1/memory/search", {"scope_id": scope_id, "query": query, "limit": limit})
    hits = resp.get("hits", []) or []
    out = [{"text": h.get("text", ""), "score": h.get("score", 0)} for h in hits]
    _log(
        "/v1/memory/search",
        {"scope_id": scope_id, "query": query[:60], "limit": limit},
        {"hits": len(out)},
    )
    return out


def is_available() -> bool:
    try:
        ensure_scope()
        return True
    except Exception:
        return False

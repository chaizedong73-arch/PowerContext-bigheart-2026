"""复习教练 RecallLoop — FastAPI 入口。

seekdb 负责知识检索（三路混合 + 出处），PowerContext 负责学习者记忆（薄弱点/偏好/续接）。
无 Key 全流程可用；PowerContext Server 未启动时相关接口优雅降级，不白屏。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import memory
import review
import store

app = FastAPI(title="复习教练 RecallLoop", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_RETRIEVAL_LOG = config.DATA_DIR / "retrieval_log.jsonl"


def _log_retrieval(record: Dict) -> None:
    config.ensure_dirs()
    record = {"ts": int(time.time()), **record}
    with open(_RETRIEVAL_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------- 请求模型 ----------------


class DocumentIn(BaseModel):
    filename: str
    content: str


class AskIn(BaseModel):
    question: str
    source: Optional[str] = None
    top_k: Optional[int] = None


class CardsIn(BaseModel):
    count: int = 5
    source: Optional[str] = None


class AnswerIn(BaseModel):
    card_id: str
    result: str  # pass | fail | fuzzy
    question: Optional[str] = None
    answer: Optional[str] = None
    knowledge: Optional[str] = None
    source: Optional[str] = None
    title: Optional[str] = None


class PreferenceIn(BaseModel):
    daily_minutes: Optional[int] = None
    focus: Optional[str] = None


# ---------------- 路由 ----------------


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/documents")
def import_document(doc: DocumentIn):
    chunks = store.import_document(doc.filename, doc.content)
    return {"chunks": chunks, "collection": config.COLLECTION_NAME, "filename": doc.filename}


@app.get("/api/documents")
def list_documents():
    return {"documents": store.list_documents(), "collection": config.COLLECTION_NAME}


@app.delete("/api/documents/{filename}")
def delete_document(filename: str):
    ok = store.delete_document(filename)
    return {"deleted": ok, "filename": filename}


@app.post("/api/ask")
def ask(req: AskIn):
    results, log_record = store.hybrid_search(req.question, source=req.source, top_k=req.top_k)
    _log_retrieval(log_record)
    return {
        "chunks": [
            {
                "text": r["text"],
                "source": r["source"],
                "title": r["title"],
                "score": r["score"],
                "signals": r["signals"],
            }
            for r in results
        ],
        "engine": "seekdb 向量+关键词+结构化混合",
    }


@app.post("/api/review/cards")
def make_cards(req: CardsIn):
    chunks = store.fetch_chunks()
    if req.source:
        chunks = [c for c in chunks if c["source"] == req.source]
    weak_sources: List[str] = []
    weak_hits: List[Dict] = []
    try:
        weak_hits = memory.search_memory("薄弱")
        weak_sources = review.weak_sources_from_hits(weak_hits, sorted({c["source"] for c in chunks}))
    except Exception:
        weak_hits, weak_sources = [], []
    cards = review.generate_cards(chunks, weak_sources, count=req.count)
    return {"cards": cards, "weak_sources": weak_sources, "memory_used": bool(weak_hits)}


@app.post("/api/review/answer")
def submit_answer(req: AnswerIn):
    event = {
        "card_id": req.card_id,
        "result": req.result,
        "question": req.question or "",
        "answer": req.answer or "",
        "knowledge": req.knowledge or "",
        "source": req.source or "",
        "title": req.title or "",
    }
    saved = review.append_event(event)
    pc = {"recorded": False, "reason": ""}
    try:
        out = memory.record_review(saved)
        pc = {"recorded": True, **out}
    except memory.PowerContextUnavailable as e:
        pc = {"recorded": False, "reason": str(e)}
    except Exception as e:
        pc = {"recorded": False, "reason": f"记忆写入失败：{e}"}
    return {"saved": True, "attempt": saved.get("attempt", 1), "powercontext": pc}


@app.post("/api/preferences")
def save_preferences(req: PreferenceIn):
    parts = []
    if req.daily_minutes:
        parts.append(f"每天复习 {req.daily_minutes} 分钟")
    if req.focus:
        parts.append(f"优先方向：{req.focus}")
    if not parts:
        return {"saved": False, "reason": "无偏好内容"}
    text = "复习偏好：" + "；".join(parts)
    try:
        memory.save_preference(text)
        return {"saved": True, "text": text}
    except memory.PowerContextUnavailable as e:
        return JSONResponse(status_code=503, content={"saved": False, "reason": str(e)})


@app.get("/api/plan")
def get_plan():
    chunks = store.fetch_chunks()
    events = review.load_events()
    context = {"status": "unavailable", "content": ""}
    weak_hits: List[Dict] = []
    try:
        context = memory.load_context("薄弱知识点 复习偏好 上次复习进度")
        weak_hits = memory.search_memory("薄弱")
    except memory.PowerContextUnavailable as e:
        context = {"status": "unavailable", "content": "", "reason": str(e)}
    except Exception as e:
        context = {"status": "unavailable", "content": "", "reason": str(e)}
    plan = review.build_plan(chunks, events, context, weak_hits)
    return {
        "plan": plan,
        "memory_context_status": context.get("status", "unavailable"),
        "memory_context": context.get("content", ""),
        "memory_reason": context.get("reason", ""),
        "weak_points": [{"text": h.get("text", ""), "score": h.get("score", 0)} for h in weak_hits],
        "stats": {
            "total_answers": len(events),
            "fail": sum(1 for e in events if e.get("result") == "fail"),
            "fuzzy": sum(1 for e in events if e.get("result") == "fuzzy"),
            "pass": sum(1 for e in events if e.get("result") == "pass"),
        },
    }


# 前端单页（挂在最后，避免覆盖 /api）
_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.exists():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")

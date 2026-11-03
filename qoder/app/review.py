"""复习流程：机械出题、本地自评记录、今日计划生成。无 Key 全流程可用。"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

import config

_REVIEWS_FILE = config.DATA_DIR / "reviews.json"

_SENT_SPLIT = re.compile(r"[。！？!?\n]")
_NUM = re.compile(r"\d+(?:\.\d+)?%?")
_EN = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")
_CJK = re.compile(r"[\u4e00-\u9fff]{2,6}")


# ---------------- 本地自评记录 ----------------


def load_events() -> List[Dict]:
    config.ensure_dirs()
    if not _REVIEWS_FILE.exists():
        return []
    try:
        return json.loads(_REVIEWS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def append_event(event: Dict) -> Dict:
    events = load_events()
    event = dict(event)
    event.setdefault("ts", int(time.time()))
    # 同一知识点的第几次作答
    same = [e for e in events if e.get("card_id") == event.get("card_id")]
    event["attempt"] = len(same) + 1
    events.append(event)
    config.ensure_dirs()
    _REVIEWS_FILE.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    return event


# ---------------- 出题（机械挖空） ----------------


def _pick_term(sentence: str) -> Optional[str]:
    for rx in (_NUM, _EN):
        m = rx.search(sentence)
        if m:
            return m.group(0)
    m = _CJK.search(sentence)
    if m:
        return m.group(0)
    return None


def _build_card(sent: str, term: str, title: str, source: str) -> Dict:
    blanked = sent.replace(term, "＿＿＿", 1)
    return {
        "question": f"关于《{title}》：{blanked}？（来源：{source}）",
        "answer": term,
        "knowledge": term,
        "answer_sentence": sent,
    }


def cloze(text: str, title: str, source: str) -> Optional[Dict]:
    """机械挖空：优先选含数字/英文专名的句子（挖空质量高），纯中文句兜底。"""
    sentences = [
        s.strip()
        for s in _SENT_SPLIT.split(text)
        if len(s.strip()) >= 8 and not s.strip().startswith("#")
    ]
    # 第一轮：数字 / 英文专名
    for sent in sentences:
        m = _NUM.search(sent) or _EN.search(sent)
        if m:
            return _build_card(sent, m.group(0), title, source)
    # 第二轮：中文术语兜底
    for sent in sentences:
        m = _CJK.search(sent)
        if m:
            return _build_card(sent, m.group(0), title, source)
    return None


def generate_cards(chunks: List[Dict], weak_sources: List[str], count: int = 5) -> List[Dict]:
    """薄弱点来源优先出题，否则按块顺序。"""
    weak = set(weak_sources)
    ordered = sorted(chunks, key=lambda c: (0 if c["source"] in weak else 1, c["source"], c["index"]))
    cards: List[Dict] = []
    seen = set()
    for c in ordered:
        if len(cards) >= count:
            break
        cz = cloze(c["text"], c["title"] or c["source"], c["source"])
        if not cz or c["id"] in seen:
            continue
        seen.add(c["id"])
        cards.append(
            {
                "card_id": c["id"],
                "question": cz["question"],
                "answer": cz["answer"],
                "answer_sentence": cz["answer_sentence"],
                "knowledge": cz["knowledge"],
                "source": c["source"],
                "title": c["title"],
                "priority": "weak" if c["source"] in weak else "normal",
            }
        )
    return cards


def weak_sources_from_hits(hits: List[Dict], known_sources: List[str]) -> List[str]:
    """从 PowerContext 薄弱点记忆文本里提取出现的资料文件名。"""
    out: List[str] = []
    blob = " ".join(h.get("text", "") for h in hits)
    for src in known_sources:
        if src and src in blob and src not in out:
            out.append(src)
    return out


# ---------------- 今日计划 ----------------


def build_plan(
    chunks: List[Dict],
    events: List[Dict],
    context: Dict,
    weak_hits: List[Dict],
) -> List[Dict]:
    plan: List[Dict] = []
    # 1) 薄弱点相关来源优先
    weak_sources = weak_sources_from_hits(weak_hits, sorted({c["source"] for c in chunks}))
    for src in weak_sources:
        titles = sorted({c["title"] for c in chunks if c["source"] == src and c.get("title")})
        plan.append(
            {
                "type": "weakness",
                "source": src,
                "reason": "PowerContext 记忆标记为薄弱点，优先复习",
                "topics": titles[:5],
            }
        )
    # 2) 久未复习 / 答错过的块
    attempts: Dict[str, int] = {}
    failed: Dict[str, int] = {}
    for e in events:
        cid = e.get("card_id")
        if not cid:
            continue
        attempts[cid] = attempts.get(cid, 0) + 1
        if e.get("result") in ("fail", "fuzzy"):
            failed[cid] = failed.get(cid, 0) + 1
    cand = sorted(
        chunks,
        key=lambda c: (failed.get(c["id"], 0) * -1, attempts.get(c["id"], 0), c["source"], c["index"]),
    )
    picked = 0
    for c in cand:
        if picked >= 5:
            break
        if c["source"] in weak_sources and picked >= 2:
            continue
        plan.append(
            {
                "type": "review",
                "source": c["source"],
                "title": c["title"],
                "reason": ("上次答错，需巩固" if failed.get(c["id"]) else "尚未复习"),
            }
        )
        picked += 1
    return plan

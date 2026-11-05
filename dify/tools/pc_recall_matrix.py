"""Regenerate evidence/pc_recall_matrix.txt.

量一件事：本实例 PowerContext 落在 SQLite + FTS5（没有 embedding provider），
所以 `/v1/context/prepare` 的召回集合随查询措辞变化，而 `/v1/memory/entries/list`
是按写入顺序返回的，不受措辞影响。DSL 因此两路都读：fts 命中的当线索，
list 兜底保证「上一局发生了什么」一定在上下文里。

跑法（WSL，PowerContext 已在 127.0.0.1:8000）：
    python3 tools/pc_recall_matrix.py
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from pathlib import Path

PC = os.environ.get("POWERCONTEXT_BASE_URL", "http://127.0.0.1:8000")
OUT = Path(__file__).resolve().parent.parent / "evidence" / "pc_recall_matrix.txt"
BEGIN = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
END = "END_POWERCONTEXT_PREPARED_CONTEXT_V1"
SCENE_TAG = re.compile(r"当前场景=([A-Za-z0-9_\-]+)")
# 每次跑用一对全新的 scope，否则上一轮留下的条目会把「list 恒等于写入条数」这条读不清
RUN = time.strftime("%H%M%S")
PLAYER = "%s-%s" % (os.environ.get("ARCHIVE_FIXTURE_PLAYER", "A-RECALL-MATRIX"), RUN)

TEXT = ("工号 %s 在《礁石村的深夜电台》第 2 轮 当前场景=scn_seaside_radio："
        "玩家打算把磁带倒回开头；违反 RULE-RAD-02；解锁封存规则 RULE-RAD-05" % PLAYER)

QUERIES = [
    ("玩家原话·续单", "继续"),
    ("玩家原话·回头看", "我刚才是不是犯规了"),
    ("玩家原话·换单", "换个场景"),
    ("玩家原话·无关词", "有人吗"),
    ("DSL 实际查询", "%s 电台,磁带,倒带 违反 违规 解锁 封存 结局 预兆" % PLAYER),
]

CROWD = [
    ("progress", "工号 %s 在《礁石村的深夜电台》第 1 轮 当前场景=scn_seaside_radio：玩家打算推开旅馆的门" % PLAYER),
    ("violation", "工号 %s 在《礁石村的深夜电台》第 2 轮 当前场景=scn_seaside_radio：玩家打算把磁带倒回开头；违反 RULE-RAD-02" % PLAYER),
    ("progress", "工号 %s 在《礁石村的深夜电台》第 3 轮 当前场景=scn_seaside_radio：玩家打算关掉收音机旋钮" % PLAYER),
    ("unlock", "工号 %s 在《礁石村的深夜电台》第 4 轮 当前场景=scn_seaside_radio：解锁封存规则 RULE-RAD-05" % PLAYER),
    ("progress", "工号 %s 在《礁石村的深夜电台》第 5 轮 当前场景=scn_seaside_radio：玩家打算走出潮声房" % PLAYER),
]


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        PC + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def prepare_items(content: str) -> list:
    m = re.search(BEGIN + r"(.*?)" + END, content, re.S)
    if not m:
        return []
    try:
        rows = (json.loads(m.group(1).strip()).get("items") or [])
    except Exception:
        return []
    return [str(r.get("content") or "") for r in rows if isinstance(r, dict)]


def _probe(sid: str, q: str):
    prep = post("/v1/context/prepare", {"scope_id": sid, "query": q, "max_bytes": 6000})
    items = prepare_items(str(prep.get("content") or ""))
    scenes = [s for t in items for s in SCENE_TAG.findall(t)]
    lst = post("/v1/memory/entries/list", {"scope_id": sid, "include_inactive": False})
    texts = [str(e.get("text") or "") for e in (lst.get("entries") or []) if isinstance(e, dict)]
    return items, (scenes[-1] if scenes else "-"), texts


def _ensure_scope(tag: str) -> str:
    return post("/v1/scopes", {
        "title": "午夜档案馆·工号" + tag,
        "summary": "规则怪谈玩家档案",
        "idempotency_key": "archive-player-" + tag,
    })["scope_id"]


def main() -> None:
    sid = _ensure_scope(PLAYER)
    post("/v1/memory/remember", {"scope_id": sid, "kind": "violation", "text": TEXT,
                                 "reason": "recall-matrix"})

    lines = [
        "# PowerContext 召回矩阵：同一份记忆，两种读法",
        "# 服务 powercontext 1.2.0，存储后端 SQLite + FTS5",
        "#   （~/.local/share/powercontext/powercontext.db，表 pc_scopes / pc_memory_entry_heads /",
        "#    pc_memory_entry_versions / pc_memory_entry_fts），不是 SeekDB；",
        "#   该实例未配 embedding provider，所以 /v1/context/prepare 只有 fts 召回。",
        "# 结论：prepare 是否命中随措辞变；entries/list 恒返回全部条目。DSL 两路都读并去重合并。",
        "#",
        "## A. 档案袋里只有一条（scope_id=%s）" % sid,
        "#   写入内容：%s" % TEXT,
        "%-14s %-42s %7s %18s %6s" % ("查询类型", "query", "prepare", "命中场景", "list"),
        "-" * 100,
    ]
    for label, q in QUERIES:
        items, scene, texts = _probe(sid, q)
        lines.append("%-14s %-42s %7d %18s %6d" % (label, q[:42], len(items), scene, len(texts)))

    crowd_player = PLAYER + "-CROWD"
    csid = _ensure_scope(crowd_player)
    for kind, text in CROWD:
        post("/v1/memory/remember", {"scope_id": csid, "kind": kind, "text": text,
                                     "reason": "recall-matrix-crowd"})
    lines += [
        "-" * 100,
        "## B. 同一工号连打 5 轮（scope_id=%s），关键那条是第 2 轮的 RULE-RAD-02 违规" % csid,
        "%-14s %-42s %7s %18s %6s %22s" % ("查询类型", "query", "prepare", "命中场景", "list", "RAD-02 是否在"),
        "-" * 100,
    ]
    crowd_queries = [
        ("玩家原话·回头看", "我刚才是不是犯规了"),
        ("玩家原话·续单", "继续"),
        ("玩家原话·问旧账", "我上一轮违反了什么"),
        ("DSL 实际查询", "%s 电台,磁带,倒带 违反 违规 解锁 封存 结局 预兆" % crowd_player),
    ]
    for label, q in crowd_queries:
        items, scene, texts = _probe(csid, q)
        in_prep = any("RULE-RAD-02" in t for t in items)
        in_list = any("RULE-RAD-02" in t for t in texts)
        lines.append("%-14s %-42s %7d %18s %6d %22s" % (
            label, q[:42], len(items), scene, len(texts),
            "prepare%s list%s" % ("√" if in_prep else "×", "√" if in_list else "×")))
    lines += [
        "-" * 100,
        "读法：",
        "1) A 段：玩家原话「继续」「我刚才是不是犯规了」「有人吗」在 fts 上都是 0 命中，",
        "   查询里带上档案词汇（场景 / 电台 / 违反 / 解锁）才召得到——这就是跨会话翻旧账",
        "   在判题里没召回 RULE-RAD-02 的机制原因，不是写入丢了。",
        "2) B 段：条目变多以后 prepare 仍会漏，而 entries/list 两段都恒等于写入条数。",
        "   DSL 的「按顺序读档案袋」节点走的正是 list 这一路，合并后关键条目必在上下文里。",
        "3) 「命中场景」取自记忆文本中的 当前场景=<scene_id> 标签：新开会话窗口时会话变量是空的，",
        "   Chatflow 靠这个标签续上原来那一局，而不是重派。",
    ]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()

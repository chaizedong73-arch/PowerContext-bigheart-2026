"""一次性：跑真实后端，产出前端演示模式内置数据 static/demo-data.js（真实运行快照，裁剪体积）。"""
import json
import os
import sys

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)
import dispatch  # noqa: E402
import pc  # noqa: E402
import store  # noqa: E402

STATIC = os.path.join(BACKEND, "static")
os.makedirs(STATIC, exist_ok=True)


def trim_evidence(ev, n=3):
    out = []
    for e in (ev or [])[:n]:
        cit = e.get("citation") or {}
        meta = e.get("meta") or {}
        out.append({
            "id": e.get("id"), "text": (e.get("text") or "")[:280], "score": e.get("score"),
            "signals": e.get("signals"),
            "meta": {k: meta.get(k) for k in ("kind", "owner", "role", "action", "status", "path", "turn")},
            "citation": {"owner": cit.get("owner"), "kind": cit.get("kind"),
                         "sessionKey": cit.get("sessionKey"), "sessionTitle": cit.get("sessionTitle"),
                         "srcFile": cit.get("srcFile"), "srcLine": cit.get("srcLine"), "at": cit.get("at")},
        })
    return out


demo = {}

# stats + sessions
demo["stats"] = store.stats()
sess = store.list_sessions()
demo["sessionCount"] = len(sess)
demo["sessions"] = sess[:6]

# search
goal = "多AI协同 会话检索 混合检索 seekdb"
res, log = store.hybrid_search(goal, top_k=5)
demo["search"] = {"query": goal, "mode": log.get("mode"), "results": trim_evidence(res, 5)}

# find_by_file（取一条真实 change 的 path 反查）
sample_path = ""
for r in res:
    p = (r.get("meta") or {}).get("path")
    if p:
        sample_path = p
        break
if not sample_path and sess:
    pass
fb = store.find_by_file(sample_path or "项目主页.md", limit=8) if sample_path else {"matchCount": 0, "matches": []}
demo["findByFile"] = {"file": fb.get("file"), "normalized": fb.get("normalized"),
                      "matchCount": fb.get("matchCount", 0),
                      "matches": [{"action": m.get("action"), "status": m.get("status"),
                                   "path": m.get("path"), "exact": m.get("exact"),
                                   "citation": m.get("citation")} for m in (fb.get("matches") or [])[:6]]}

# dispatch closed loop（真实跑一次）
rec = dispatch.run_closed_loop(goal, num_agents=2, max_rounds=2)
tasks = []
for t in rec.get("tasks", []):
    tasks.append({
        "taskId": t["taskId"], "owner": t["target"]["owner"],
        "sessionKey": t["target"]["sessionKey"], "sessionTitle": t["target"]["sessionTitle"],
        "workingDir": t["target"]["workingDir"], "mode": t["mode"],
        "objective": t["objective"], "query": t["query"],
        "rounds": t["rounds"], "quality": t["quality"], "receipt": t["receipt"],
        "evidenceVersion": t["evidenceVersion"], "evidence": trim_evidence(t.get("evidence"), 3),
    })
demo["dispatch"] = {"parentId": rec.get("parentId"), "goal": rec.get("goal"), "status": rec.get("status"),
                    "targets": rec.get("targets"), "tasks": tasks,
                    "crosscheck": rec.get("crosscheck"), "finalize": rec.get("finalize")}

# memory search + context + handoff readback
try:
    ms = pc.search(goal, limit=5, mode="auto")
    demo["memorySearch"] = {"mode": ms.get("mode"),
                            "hits": [{"text": (h.get("text") or "")[:200], "score": h.get("score"),
                                      "matched_by": h.get("matched_by")} for h in ms.get("hits", [])[:5]]}
except Exception as e:
    demo["memorySearch"] = {"error": str(e)}
try:
    ctx = pc.prepare_context("多AI协同 共享记忆 交接 进度", max_bytes=1200)
    demo["memoryContext"] = {"status": ctx.get("status"), "content": (ctx.get("content") or "")[:800]}
except Exception as e:
    demo["memoryContext"] = {"status": "unavailable", "content": "", "error": str(e)}
try:
    ho = pc.read_handoff(selection="latest")
    checks = ho.get("evidence_checks") or []
    demo["handoff"] = {"status": ho.get("status"),
                       "objective": (ho.get("content") or {}).get("objective"),
                       "disposition": (ho.get("content") or {}).get("disposition"),
                       "stateCount": len((ho.get("content") or {}).get("state") or []),
                       "evidenceAvailable": "%d/%d" % (sum(1 for c in checks if c.get("status") == "available"), len(checks)),
                       "evidenceChecks": [{"claim": c.get("claim"), "stateIndex": c.get("state_index"),
                                           "status": c.get("status")} for c in checks[:6]]}
except Exception as e:
    demo["handoff"] = {"error": str(e)}

js = "// 多AI协同 v2 · 演示模式内置数据（由 backend/_capture_demo.py 对真实后端跑出的快照，已裁剪体积）\n"
js += "// 这些是 REAL 运行结果：真实 seekdb 检索命中、真实派工闭环记录、真实 PowerContext 交接核验。\n"
js += "window.DEMO = " + json.dumps(demo, ensure_ascii=False, indent=2) + ";\n"
with open(os.path.join(STATIC, "demo-data.js"), "w", encoding="utf-8") as f:
    f.write(js)
print("demo-data.js written:", len(js), "bytes")
print("stats:", json.dumps(demo["stats"], ensure_ascii=False))
print("search hits:", len(demo["search"]["results"]), "mode:", demo["search"]["mode"])
print("findByFile matchCount:", demo["findByFile"]["matchCount"])
print("dispatch tasks:", len(demo["dispatch"]["tasks"]), "handoff:", json.dumps(demo["handoff"], ensure_ascii=False)[:200])

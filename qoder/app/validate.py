"""集成验证脚本：对着运行中的应用跑 V1-V3，并直接 curl PowerContext 核对记忆。

用法（WSL2，应用与 PowerContext Server 均已启动）：
  python qoder/app/validate.py
V4（跨会话续接）需重启应用后单独跑，见 validate_v4.py。
"""

import json
import os
import sys

import httpx

APP = os.environ.get("APP_URL", "http://127.0.0.1:8100")
PC = os.environ.get("POWERCONTEXT_URL", "http://127.0.0.1:8000")
DEMO = os.path.join(os.path.dirname(__file__), "demo-data")


def main():
    c = httpx.Client(timeout=120)
    ok = True

    print("=== V1 资料导入 + seekdb 索引 ===")
    for fn in ("os-basics.md", "study-notes.md"):
        with open(os.path.join(DEMO, fn), encoding="utf-8") as f:
            content = f.read()
        r = c.post(f"{APP}/api/documents", json={"filename": fn, "content": content}).json()
        print(f"  import {fn} -> chunks={r.get('chunks')} collection={r.get('collection')}")
        if not r.get("chunks"):
            ok = False
    docs = c.get(f"{APP}/api/documents").json()["documents"]
    print("  list:", [(d["filename"], d["chunks"]) for d in docs])
    print("  seekdb dir:", os.path.isdir(os.path.expanduser("~/recallloop-data/seekdb")))

    print("\n=== V2 混合检索问答（带出处）===")
    q = "seekdb 和 PowerContext 在本项目中如何分工？"
    r = c.post(f"{APP}/api/ask", json={"question": q}).json()
    for i, ch in enumerate(r["chunks"]):
        sig = ch["signals"]
        print(f"  #{i+1} score={ch['score']} vector={sig['vector']} keyword={sig['keyword']} "
              f"src={ch['source']} title={ch['title']}")
        print(f"      {ch['text'][:70]}".replace("\n", " "))
    top_src = r["chunks"][0]["source"] if r["chunks"] else ""
    print("  top1 source:", top_src, "| engine:", r.get("engine"))
    r2 = c.post(f"{APP}/api/ask", json={"question": q, "source": "os-basics.md"}).json()
    all_os = all(ch["source"] == "os-basics.md" for ch in r2["chunks"])
    print("  source filter os-basics.md -> all match:", all_os, "n=", len(r2["chunks"]))
    if not all_os:
        ok = False

    print("\n=== V3 复习自测 + PowerContext 记忆写入 ===")
    cards = c.post(f"{APP}/api/review/cards", json={"count": 5}).json()
    print("  cards:", len(cards["cards"]), "| weak_sources:", cards.get("weak_sources"),
          "| memory_used:", cards.get("memory_used"))
    # 自评：前2张 pass，接2张 fail，最后1张 fuzzy
    results = ["pass", "pass", "fail", "fail", "fuzzy"]
    for i, card in enumerate(cards["cards"][:5]):
        res = results[i] if i < len(results) else "pass"
        ans = c.post(f"{APP}/api/review/answer", json={
            "card_id": card["card_id"], "result": res, "question": card["question"],
            "answer": card["answer"], "knowledge": card.get("knowledge", card["answer"]),
            "source": card["source"], "title": card["title"],
        }).json()
        print(f"  card{i+1} [{card['source']}] -> {res} | pc={ans.get('powercontext')}")

    print("\n=== 证据日志核对 ===")
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    rlog = os.path.join(data_dir, "retrieval_log.jsonl")
    mlog = os.path.join(data_dir, "memory_log.jsonl")
    if os.path.exists(rlog):
        lines = open(rlog, encoding="utf-8").read().strip().split("\n")
        print(f"  retrieval_log.jsonl: {len(lines)} 行；样例:")
        print("   ", lines[-1][:200])
    else:
        print("  retrieval_log.jsonl 缺失"); ok = False
    if os.path.exists(mlog):
        lines = open(mlog, encoding="utf-8").read().strip().split("\n")
        eps = {}
        for ln in lines:
            try:
                e = json.loads(ln)["endpoint"]
                eps[e] = eps.get(e, 0) + 1
            except Exception:
                pass
        print(f"  memory_log.jsonl: {len(lines)} 行；端点统计: {eps}")
    else:
        print("  memory_log.jsonl 缺失"); ok = False

    # 直接 curl PowerContext memory/search 核对薄弱点
    scope_id = os.environ.get("POWERCONTEXT_SCOPE_ID", "")
    scope_file = os.path.join(data_dir, "scope.json")
    if not scope_id and os.path.exists(scope_file):
        scope_id = json.load(open(scope_file, encoding="utf-8"))["scope_id"]
    if scope_id:
        sr = c.post(f"{PC}/v1/memory/search", json={"scope_id": scope_id, "query": "薄弱"}).json()
        hits = sr.get("hits", [])
        print(f"  curl /v1/memory/search '薄弱' -> {len(hits)} 条")
        for h in hits[:3]:
            print("    -", h.get("text", "")[:80])
        if not hits:
            ok = False

    print("\n=== 结果:", "PASS" if ok else "有问题需排查", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

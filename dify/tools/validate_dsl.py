"""Offline validator for the 午夜档案馆 Chatflow DSL.

Checks (1) YAML/graph structure, (2) every {{#node.var#}} reference resolves to a
declared output, (3) each code node compiles, (4) code nodes run correctly against
real capture files from the live seekdb / PowerContext services.

Run with a python that has PyYAML, e.g. WSL: ~/recallloop-venv/bin/python
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

DSL = Path(sys.argv[1] if len(sys.argv) > 1 else Path.cwd() / "dsl/midnight_archive_chatflow.yml")
CAPTURE = Path(sys.argv[2] if len(sys.argv) > 2
               else Path(__file__).resolve().parent.parent / "capture")

errors: list[str] = []
warns: list[str] = []

REF = re.compile(r"\{\{#([^.#}]+)\.([^#}]+)#\}\}")

HTTP_OUTPUTS = {"body", "status_code", "headers", "files"}
LLM_OUTPUTS = {"text", "usage", "files"}
AGG_OUTPUTS = {"output"}
START_SYS = {"sys.query", "sys.files", "sys.user_id", "sys.conversation_id",
             "sys.dialogue_count", "sys.app_id", "sys.workflow_id", "sys.workflow_run_id"}


def err(msg: str) -> None:
    errors.append(msg)


def walk(node, path=""):
    """Yield (path, value) for every string in the graph."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def main() -> int:
    doc = yaml.safe_load(DSL.read_text(encoding="utf-8"))
    if doc.get("kind") != "app":
        err(f"kind must be app, got {doc.get('kind')!r}")
    if str(doc.get("version")) not in ("0.1.5", "0.7.0"):
        warns.append(f"app version = {doc.get('version')!r}")
    wf = doc["workflow"]
    graph = wf["graph"]
    nodes = graph["nodes"]
    edges = graph["edges"]

    for i, n in enumerate(nodes):
        if not isinstance(n, dict) or "id" not in n or "data" not in n:
            err(f"nodes[{i}] is not a {{id, data}} mapping: {str(n)[:120]}")
    nodes = [n for n in nodes if isinstance(n, dict) and "id" in n and "data" in n]
    ids = [n["id"] for n in nodes]
    if len(ids) != len(set(ids)):
        err("duplicate node ids")
    by_id = {n["id"]: n for n in nodes}
    types = {n["id"]: n["data"]["type"] for n in nodes}
    print(f"nodes={len(nodes)} edges={len(edges)}")
    for n in nodes:
        print(f"  {n['id']:<14} {n['data']['type']:<20} {n['data'].get('title', '')}")

    # --- graph structure
    starts = [n["id"] for n in nodes if types[n["id"]] == "start"]
    if len(starts) != 1:
        err(f"need exactly one start node, got {starts}")
    answers = [n["id"] for n in nodes if types[n["id"]] == "answer"]
    if not answers:
        err("advanced-chat needs at least one answer node")
    for e in edges:
        for side, key in (("source", "source"), ("target", "target")):
            if e[key] not in by_id:
                err(f"edge {e.get('id')} refers to unknown node {e[key]!r}")
        if e.get("sourceHandle") not in ("source", "true", "false") and not str(e.get("sourceHandle")).startswith("case"):
            warns.append(f"edge {e.get('id')} has unusual sourceHandle {e.get('sourceHandle')!r}")
        src = e["source"]
        if src in types and types[src] == "if-else":
            case_ids = {c["id"] for c in by_id[src]["data"]["cases"]}
            if e["sourceHandle"] != "false" and e["sourceHandle"] not in case_ids:
                err(f"edge {e['id']}: sourceHandle {e['sourceHandle']!r} not an if-else case id {case_ids}")

    # every non-start node must have an incoming edge, every non-answer an outgoing one
    incoming = {e["target"] for e in edges}
    outgoing = {e["source"] for e in edges}
    for nid in ids:
        if types[nid] != "start" and nid not in incoming:
            err(f"node {nid} has no incoming edge (unreachable)")
        if types[nid] != "answer" and nid not in outgoing:
            err(f"node {nid} has no outgoing edge (dead end)")

    # branch reachability: aggregator needs both branch sources; branches need to rejoin
    agg = [n for n in nodes if types[n["id"]] == "variable-aggregator"]
    for a in agg:
        for var in a["data"]["variables"]:
            if var[0] not in by_id:
                err(f"aggregator {a['id']} refers to unknown node {var[0]!r}")

    # --- declared outputs per node
    declared: dict[str, set[str]] = {}
    for n in nodes:
        nid = n["id"]
        t = types[nid]
        d = n["data"]
        if "-vv" in sys.argv:
            print("PAIR", nid, "data.type=", d.get("type"), "types[nid]=", t,
                  "outputs=", sorted((d.get("outputs") or {}).keys()))
        if t == "code":
            declared[nid] = set((d.get("outputs") or {}).keys())
        elif t == "llm":
            declared[nid] = set(LLM_OUTPUTS)
        elif t == "http-request":
            declared[nid] = set(HTTP_OUTPUTS)
        elif t == "variable-aggregator":
            declared[nid] = set(AGG_OUTPUTS)
        elif t == "start":
            declared[nid] = {v["variable"] for v in d.get("variables") or []}
        elif t == "template-transform":
            declared[nid] = {"text"}
        else:
            declared[nid] = set()

    conv = {v["name"] for v in wf.get("conversation_variables") or []}
    env = {v["name"] for v in wf.get("environment_variables") or []}
    if "-v" in sys.argv:
        for nid in ids:
            print("declared", nid, types[nid], sorted(declared.get(nid, [])))

    # --- reference resolution
    refs = {}
    for path, text in walk(graph):
        for src, var in REF.findall(text):
            refs.setdefault((src, var), []).append(path)
    print(f"distinct variable references = {len(refs)}")
    for (src, var), paths in sorted(refs.items()):
        key = f"{src}.{var}"
        if src == "env":
            if var not in env:
                err(f"undeclared env var {key} (used at {paths[0]})")
        elif src == "conversation":
            if var not in conv:
                err(f"undeclared conversation var {key} (used at {paths[0]})")
        elif src == "sys":
            if f"sys.{var}" not in START_SYS:
                warns.append(f"unusual system var {key} (used at {paths[0]})")
        elif src in by_id:
            if var not in declared[src]:
                err(f"{key} not a declared output of {src} ({types[src]}); used at {paths[0]}")
        else:
            err(f"reference {key} points to unknown node id {src!r} (used at {paths[0]})")

    # conversation vars written by assigner must exist
    for n in nodes:
        if types[n["id"]] == "assigner":
            for item in n["data"].get("items") or []:
                sel = item["variable_selector"]
                if sel[0] != "conversation" or sel[1] not in conv:
                    err(f"assigner {n['id']} writes unknown var {sel}")
                if item.get("input_type") == "variable":
                    src, var = item["value"][0], item["value"][-1]
                    if src == "conversation":
                        if var not in conv:
                            err(f"assigner {n['id']} reads unknown conversation var {var}")
                    elif src not in by_id:
                        err(f"assigner {n['id']} reads unknown node {src}")
                    elif var not in declared[src]:
                        err(f"assigner {n['id']} reads undeclared output {src}.{var}")

    # code nodes must compile and main() params must match declared variables
    compiled = {}
    for n in nodes:
        if types[n["id"]] != "code":
            continue
        nid = n["id"]
        src_code = n["data"]["code"]
        try:
            code = compile(src_code, f"<{nid}>", "exec")
        except SyntaxError as exc:
            err(f"code node {nid} does not compile: {exc}")
            continue
        ns: dict = {}
        exec(code, ns)
        if "main" not in ns:
            err(f"code node {nid} has no main()")
            continue
        import inspect

        params = [p for p in inspect.signature(ns["main"]).parameters]
        provided = [v["variable"] for v in n["data"].get("variables") or []]
        missing = [p for p in params if p not in provided]
        extra = [p for p in provided if p not in params]
        if missing:
            err(f"code node {nid}: main() params not provided by node vars: {missing}")
        if extra:
            warns.append(f"code node {nid}: node vars not used by main(): {extra}")
        declared_out = set((n["data"].get("outputs") or {}).keys())
        compiled[nid] = (ns["main"], declared_out, n["data"].get("variables") or [])

    # --- behavioural dry run against captured real payloads
    def load(name):
        p = CAPTURE / name
        return p.read_text(encoding="utf-8") if p.exists() else None

    if compiled:
        scene_body = load("scene_pack.json")
        search_body = load("search.json")
        prepare_body = load("prepare.json")
        scopes_body = load("scopes.json")

        if scopes_body is not None:
            out = compiled["scope_code"][0](body=scopes_body, status="201", fallback="",
                                            player_tag="A-1101", keywords_csv="电梯,对讲机")
            print("scope_code ->", json.dumps(out, ensure_ascii=False))
            if not isinstance(out, dict) or not out.get("scope_id"):
                err("scope_code did not return a usable scope_id dict")
            else:
                prepped = json.loads(out["prepare_body"])
                if prepped.get("scope_id") != out["scope_id"] or "电梯" not in prepped.get("query", ""):
                    err(f"scope_code.prepare_body wrong: {prepped}")

        if prepare_body is not None:
            entries_body = load("entries_list.json") or '{"entries": []}'
            out = compiled["memory_code"][0](body=prepare_body, entries_raw=entries_body)
            if not isinstance(out, dict):
                err(f"memory_code returned {type(out).__name__}, Dify requires dict")
            else:
                if set(out) != {"memory_block", "memory_count", "active_scene_id"}:
                    err(f"memory_code keys {set(out)} != declared outputs")
                print("memory_code -> count=", out.get("memory_count"), "active_scene_id=", out.get("active_scene_id"))
                print((out.get("memory_block") or "")[:400])

        # reroll 只认玩家原话里的显式换单说法；「继续」必须留在当前场景。
        intent = compiled["intent_code"][0]
        for q, want in [("继续", 0), ("接着讲", 0), ("继续上一局", 0), ("换个场景", 1),
                        ("重新派单吧", 1), ("给我来点新的", 1),
                        # 云端实测：A-CLOUD-S11 说「继续上一局，别重派」被当成重派，
                        # 场景从《白杨便利的第十一夜》换成了自习室——否定式不能算换单。
                        ("继续上一局，别重派。", 0), ("不要换场景", 0), ("别给我换个场景", 0),
                        ("不想重开，就这个", 0), ("先别重新派单", 0), ("没换场景的意思", 0),
                        ("换个场景吧，别重派上一局", 1), ("这个太简单，换一个难的", 1)]:
            got = intent(raw=json.dumps({"intent": "start", "keywords": ["门"], "action": "玩家打算继续",
                                        "difficulty": 0}, ensure_ascii=False), query=q)["reroll"]
            if got != want:
                err(f"intent_code reroll(«{q}»)={got}, expected {want}")

        # 难度同样只认原话：云端 s16 实测同一句「难度中等」有时被模型抽成 3、有时漏成 0，
        # 漏抽等于撤掉后端的难度过滤，要中等的玩家直接派到了难度 5 的便利店。
        for q, want in [("我喜欢跟声音有关的线索，难度只要最简单的。", 1),
                        ("我喜欢跟声音有关的线索，难度中等。", 3),
                        ("给我派个活，我怕黑，喜欢跟声音有关的线索，难度中等。", 3),
                        ("来个最难的，我胆子大，越恐怖越好。", 5),
                        # 先说出口的那一档才算要求，后半句的否定是补充说明。
                        ("难度中等，别太恐怖。", 3),
                        ("不要太恐怖，给我换个轻松点的活。", 1),
                        # 名词在词的后面也算（云端 s10 第 1 轮就是这句「中等难度」）。
                        ("给我派个跟声音有关的活，中等难度。", 3),
                        # 评价当前这一幕 ≠ 要求下一幕：这句必须留给模型，不能按字面压到 1。
                        ("这个太简单，换一个难的。", 0),
                        ("随便派一个。", 0), ("", 0)]:
            got = intent(raw=json.dumps({"intent": "start", "keywords": ["声音"],
                                        "action": "玩家要求派单", "difficulty": 0},
                                       ensure_ascii=False), query=q)["difficulty"]
            if got != want:
                err(f"intent_code difficulty(«{q}»)={got}, expected {want}")
        # 模型抽对了也不能被原话带偏；但行动轮的叙述词不该变成难度上限。
        if intent(raw=json.dumps({"intent": "start", "difficulty": 2}, ensure_ascii=False),
                  query="难度中等")["difficulty"] != 3:
            err("intent_code kept the model's difficulty over the player's literal 难度中等")
        act_diff = intent(raw=json.dumps({"intent": "act", "difficulty": 0}, ensure_ascii=False),
                          query="我像往常一样推开便利店的门")["difficulty"]
        if act_diff != 0:
            err("intent_code let an act-turn narration word set the difficulty cap")
        resume_out = compiled["req2_code"][0](
            query="继续", action="玩家打算继续查看电台", keywords_csv="电台,磁带", scene_id="",
            revealed="", reroll=0, top_k=6, difficulty=0, remembered_scene="scn_seaside_radio")
        resumed = json.loads(resume_out["scene_body"])
        if resumed.get("scene_id") != "scn_seaside_radio":
            err(f"req2_code did not resume the remembered scene: {resumed}")
        if json.loads(compiled["req2_code"][0](
                query="换个场景", action="玩家打算换单", keywords_csv="电梯", scene_id="scn_seaside_radio",
                revealed="", reroll=1, top_k=6, difficulty=3, remembered_scene="")["scene_body"]).get("scene_id"):
            err("req2_code kept a scene while the player explicitly asked to reroll")
        # 换单还得把上一幕交给后端排除：「换个场景」是没有口味词的，不排除时向量最近的
        # 那一幕常常还是刚才那一幕（云端 s10 轮4 实测踩过）。
        excluded = json.loads(compiled["req2_code"][0](
            query="换个场景", action="玩家打算换单", keywords_csv="电梯", scene_id="",
            revealed="RULE-RAD-05", reroll=1, top_k=6, difficulty=0,
            remembered_scene="scn_seaside_radio")["scene_body"])
        if excluded.get("scene_id") or excluded.get("exclude_scene_id") != "scn_seaside_radio":
            err(f"reroll must exclude the previous scene for the backend: {excluded}")
        if excluded.get("revealed_csv") != "RULE-RAD-05":
            err(f"reroll dropped the already-unsealed codes: {excluded}")
        # 场景来源得能区分「开局」和「换单」：新会话第一回合没有上一幕可排除，
        # 早先一律标 reroll，取证里看着像每局开局都被重派了一次。
        for label, kw, want in (
                ("fresh 开局", dict(query="给我派个活", scene_id="", reroll=0, remembered_scene=""), "fresh"),
                ("续会话", dict(query="继续", scene_id="scn_seaside_radio", reroll=0, remembered_scene=""), "conversation"),
                ("跨会话续局", dict(query="继续", scene_id="", reroll=0, remembered_scene="scn_seaside_radio"), "memory"),
                ("显式换单", dict(query="换个场景", scene_id="scn_seaside_radio", reroll=1, remembered_scene=""), "reroll")):
            got = compiled["req2_code"][0](action="玩家行动", keywords_csv="", revealed="",
                                          top_k=6, difficulty=0, **kw)["scene_source"]
            if got != want:
                err(f"scene_source({label})={got}, expected {want}")

        for label, body in (("scene", scene_body), ("search", search_body)):
            if body is None:
                continue
            out = compiled["archive_code"][0](body=body, revealed_in="", scene_id_in="", scene_title_in="")
            if not isinstance(out, dict):
                err(f"archive_code ({label}) returned non-dict")
                continue
            need = {"block", "scene_id", "scene_title", "revealed_csv", "codes_csv", "locked_csv", "cite_line"}
            if set(out) != need:
                err(f"archive_code ({label}) keys {set(out) ^ need}")
            print(f"\n=== archive_code[{label}] cite: {out['cite_line']}")
            print(f"codes_csv={out['codes_csv']}\nlocked_csv={out['locked_csv']}")
            print(out["block"][:900])
            for locked_code in [c for c in (out["locked_csv"] or "").split(",") if c]:
                if locked_code not in out["block"]:
                    err(f"archive_code({label}) sealed {locked_code} not shown in block")
                if locked_code in (out["codes_csv"] or ""):
                    err(f"archive_code({label}) sealed {locked_code} leaked into citable codes")
                if locked_code + "《" in out["block"]:
                    err(f"archive_code({label}) sealed {locked_code} carries a title (spoiler)")
            if label == "scene" and not out["codes_csv"]:
                err("scene pack produced no citable codes")
            if label == "search" and not out["codes_csv"]:
                warns.append("search produced no hits for the sample query")

        # host_code is the anti-hallucination gate: only codes present in the
        # retrieved archive may survive, and the LLM answers as real JSON arrays.
        if scene_body:
            arc = compiled["archive_code"][0](body=scene_body, revealed_in="",
                                              scene_id_in="", scene_title_in="")
        else:
            arc = {"codes_csv": "RULE-RAD-02,OMEN-RAD-01,END-RAD-03", "locked_csv": "RULE-RAD-05",
                   "scene_title": "合成测试场景", "scene_id": "", "revealed_csv": ""}
        allowed_codes = [c for c in arc["codes_csv"].split(",") if c]
        locked_codes = [c for c in arc["locked_csv"].split(",") if c]
        good_rule = next((c for c in allowed_codes if c.startswith("RULE-")), allowed_codes[0])
        good_omen = next((c for c in allowed_codes if c.startswith("OMEN-")), good_rule)
        good_end = next((c for c in allowed_codes if c.startswith("END-")), "")
        good_locked = locked_codes[0] if locked_codes else "RULE-XX-99"
        host = compiled["host_code"][0]

        def run_host(answer, **over):
            kwargs = dict(raw=json.dumps(answer, ensure_ascii=False), codes_csv=arc["codes_csv"],
                          locked_csv=arc["locked_csv"], revealed_in="", violations_in="RULE-ELV-01",
                          endings_in="", turn=3, scene_title=arc["scene_title"] or "测试场景",
                          scene_id=arc["scene_id"] or "scn_test",
                          player_id="A-1101", action="玩家打算违反一条规则", intent="act",
                          scope_id="scp_test_1")
            kwargs.update(over)
            return host(**kwargs)

        cases = [
            ("违规+幻觉编号", {
                "narration": "电梯在13层停住了。", "triggered": [good_rule, "RULE-HALLUCINE-99"],
                "near": [good_omen, "OMEN-FAKE-01"], "sealed_hint": good_locked,
                "revealed_now": [good_locked, "RULE-FAKE-02"], "ending": good_end, "mood": "doomed",
             }, [("violations", good_rule), ("no-hallucine", "RULE-HALLUCINE-99"),
                 ("no-fake-omen", "OMEN-FAKE-01"), ("revealed", good_locked),
                 ("no-fake-reveal", "RULE-FAKE-02"), ("mem_kind", "violation"),
                 ("endings", good_end), ("stage", "ended"), ("turn", "4")]),
            ("仅解锁封存", {
                "narration": "你把工牌举到反光板前。", "triggered": [], "near": [],
                "sealed_hint": "", "revealed_now": [good_locked], "ending": "", "mood": "safe",
             }, [("revealed", good_locked), ("mem_kind", "unlock"), ("stage", "playing")]),
            ("空返回", {"narration": "", "triggered": [], "near": [], "sealed_hint": "",
                        "revealed_now": [], "ending": "", "mood": ""},
             [("narration-fallback", "档案馆的灯闪了一下")]),
            ("逗号串而非数组", {
                "narration": "你听见背后有呼吸。", "triggered": good_rule, "near": good_omen,
                "sealed_hint": "", "revealed_now": "", "ending": "", "mood": "uneasy",
             }, [("violations", good_rule), ("mem_kind", "violation")]),
        ]
        for label, answer, checks in cases:
            try:
                out = run_host(answer)
            except Exception as exc:  # noqa: BLE001
                err(f"host_code[{label}] crashed: {exc!r}")
                continue
            if not isinstance(out, dict):
                err(f"host_code[{label}] returned {type(out).__name__}")
                continue
            if set(out) != compiled["host_code"][1]:
                err(f"host_code[{label}] keys differ from declared: {set(out) ^ compiled['host_code'][1]}")
            blob = json.dumps(out, ensure_ascii=False)
            if str(out.get("narration", "")).lstrip().startswith(("{", "[")):
                err(f"host_code[{label}] leaked the raw model payload into narration")
            try:
                mem = json.loads(out["remember_body"])
            except Exception as exc:  # noqa: BLE001
                err(f"host_code[{label}] remember_body is not valid JSON: {exc!r}")
            else:
                if mem != {"scope_id": "scp_test_1", "kind": out["mem_kind"],
                           "text": out["mem_text"], "reason": "dify-chatflow-turn"}:
                    err(f"host_code[{label}] remember_body mismatch: {mem}")
            if "当前场景=" not in out["mem_text"]:
                err(f"host_code[{label}] memory text has no 当前场景 tag, cross-session resume would break")
            for name, expect in checks:
                if name.startswith("no-"):
                    if expect in blob:
                        err(f"host_code[{label}] kept un-retrieved code {expect!r} ({name})")
                elif expect not in blob:
                    err(f"host_code[{label}] check {name} failed, expected {expect!r} in {blob[:300]}")
            print(f"\n=== host_code[{label}] -> {blob[:330]}")

        # 封存编号泄露闸门：模型把未解锁编号写进叙述时，必须在出文本前抹掉
        leak = run_host({"narration": "你迟早会撞上 %s，别回头。" % good_locked, "triggered": [],
                         "near": [], "sealed_hint": "", "revealed_now": [], "ending": "",
                         "mood": "uneasy"})
        if good_locked in leak["narration"]:
            err("host_code 没有抹掉叙述里泄露的封存编号")
        if leak["narration_redacted"] != good_locked:
            err(f"host_code 泄露闸门没记账: {leak['narration_redacted']!r}")
        unlocked = run_host({"narration": "你终于读懂了 %s。" % good_locked, "triggered": [],
                             "near": [], "sealed_hint": "", "revealed_now": [], "ending": "",
                             "mood": "safe"}, revealed_in=good_locked)
        if good_locked not in unlocked["narration"] or unlocked["narration_redacted"]:
            err(f"已解锁的编号不该被抹掉: {unlocked['narration']!r} / {unlocked['narration_redacted']!r}")

        # 推理正文泄露闸门：云端 deepseek-flash 会把思考写成
        # "<think>\n<!--dify-deepseek-reasoning-->…"（没有闭合标签）。旧版兜底在裁决
        # JSON 解析失败时把整段原文当叙述吐给玩家——cloud_run_s10 第 2/4 轮实测泄露过。
        reasoning = ('<think>\n<!--dify-deepseek-reasoning-->封存 2 条，大概是 %s 与 RULE-RAD-05，'
                     '我不能把编号区间报给玩家。' % good_locked)
        verdict = {"narration": "收音机自己亮了。", "triggered": [], "near": [],
                   "sealed_hint": "", "revealed_now": [], "ending": "", "mood": "uneasy"}
        for label, messy, want_said in (
                ("思考+裁决", reasoning + "\n" + json.dumps(verdict, ensure_ascii=False), "收音机自己亮了。"),
                # 贪婪 {.*} 会被思考正文里的花括号带偏，配平扫描才捞得到真正的裁决对象
                ("思考里带花括号", reasoning + '（比如 {"编号": 未写下}）\n' + json.dumps(verdict, ensure_ascii=False),
                 "收音机自己亮了。"),
                ("只有思考没有裁决", reasoning, "")):
            out = run_host({"narration": ""}, raw=messy)
            blob = json.dumps(out, ensure_ascii=False)
            if "think>" in blob or "dify-deepseek-reasoning" in blob:
                err(f"host_code[{label}] 把推理标记吐给了玩家: {blob[:220]}")
            if "编号区间" in blob or "我不能" in blob:
                err(f"host_code[{label}] 泄露了主持人的内心独白: {blob[:220]}")
            if good_locked in out["narration"] or "RULE-RAD-05" in out["narration"]:
                err(f"host_code[{label}] 叙述里带着封存编号: {out['narration'][:120]}")
            if want_said:
                if out["narration"] != want_said:
                    err(f"host_code[{label}] 没解析出裁决 JSON: {out['narration'][:120]!r}")
                if out["mood"] != "uneasy":
                    err(f"host_code[{label}] 裁决里的 mood 丢了: {out['mood']!r}")
            elif out["narration"] not in (
                    "档案馆的灯闪了一下。主持人没有回应你的描述，请你把动作说得更具体些。",
                    "主持人笔下的那行字被潮气泡花了，没能递到你手上。请把动作说得更具体些，或换个说法再来一次。"):
                err(f"host_code[{label}] 兜底叙述不该是模型原文: {out['narration'][:160]!r}")

        # 云端实测的另一种坏形状：主持人节点开着对话记忆（window=10）时会照着自己上一轮的
        # 「玩家可见文本」续写——状态条都拼全了，却没有裁决 JSON（cloud_run_s10 第 3 轮）。
        # 那时整轮塌成固定文案，玩家看到「主持人没有回应」。_prose 只认 think 成对闭合后的
        # 正文：截掉模型自己拼的状态条，封存编号仍要过同一道闸门；没有闭合标签照旧回固定文案。
        footer = ("\n\n---\n🪪 工号 `A-1101` ｜ 🎬 测试场景 ｜ 第 4 轮 ｜ 心情 uneasy\n"
                  "📁 档案引用：本轮引用：无\n🧠 已写入 PowerContext 记忆（kind=`progress`，scope=`scp_test_1`）\n")
        for label, messy, must_keep in (
                ("续写状态条+闭合思考", reasoning + "\n</think>\n指针停在“2”，女声开始报歌名，报完留三秒空白。"
                 "铁皮壳越来越凉，像刚被人从海里捞出来。你迟早会撞上 " + good_locked + "，别回头。"
                 "你可以把旋钮逆时针旋到底，也可以伸手按住它不让它跳。" + footer, "指针停在"),
                ("纯叙述没有JSON", "指针停在“2”，女声开始报歌名，报完留三秒空白，连电流都轻下去。"
                 "屋外的潮声把窗框震得打颤，盐味从门缝里挤进来。你可以把旋钮逆时针旋到底，"
                 "或者伸手按住它——只是你迟早会撞上 " + good_locked + "，这一条还没被写下来。", "三秒空白")):
            out = run_host({"narration": ""}, raw=messy)
            say = out["narration"]
            if must_keep not in say:
                err(f"host_code[{label}] 没保住可用叙述: {say[:160]!r}")
            if "🪪" in say or "已写入 PowerContext" in say:
                err(f"host_code[{label}] 把模型自己拼的状态条吐给了玩家: {say[:200]!r}")
            if "think>" in say or "dify-deepseek-reasoning" in say or "我不能" in say:
                err(f"host_code[{label}] 泄露了推理正文: {say[:160]!r}")
            if good_locked in say or out["narration_redacted"] != good_locked:
                err(f"host_code[{label}] 正文兜底绕过了封存闸门: {say[:120]!r} / {out['narration_redacted']!r}")
        print("\n=== host_code[正文兜底] 两条坏形状都保住叙述、截掉状态条、过了编号闸门")

        # 行动轮（search 分支）必须把「仍封存」的编号交回主持人。云端 s12/s13 实测过一版
        # locked_csv 恒为空，于是 _only(revealed_now, locked) 永远筛光——解锁在这条链路上
        # 结构性不可能（状态条「🔓 已解锁封存」永远是空的）。fixture 是后端真实响应，
        # 里面的 sealed_gated 来自 seekdb 命中被挡 + 本场景 where 查询，所以这条钉的是真链路。
        if search_body:
            s_arc = compiled["archive_code"][0](body=search_body, revealed_in="",
                                                scene_id_in="scn_office_elevator",
                                                scene_title_in="末班电梯只停偶数层")
            s_locked = [c for c in s_arc["locked_csv"].split(",") if c]
            if not s_locked:
                err("act-turn archive block carries no sealed codes: unlock can never fire")
            if "封存中" not in s_arc["block"]:
                err("act-turn block has no 封存中 section for the host to reason about")
            for c in s_locked:
                if c + "《" in s_arc["block"]:
                    err(f"act-turn sealed {c} carries a title (spoiler)")
                if c in s_arc["codes_csv"].split(","):
                    err(f"act-turn sealed {c} also citable")
            unlock = host(raw=json.dumps({"narration": "你把硬皮本翻到凌晨 0:00 那一行，只喊了一个姓。",
                                          "triggered": [], "near": [], "sealed_hint": "",
                                          "revealed_now": [s_locked[0]], "ending": "", "mood": "uneasy"},
                                         ensure_ascii=False),
                          codes_csv=s_arc["codes_csv"], locked_csv=s_arc["locked_csv"], revealed_in="",
                          violations_in="", endings_in="", turn=2, scene_title=s_arc["scene_title"],
                          scene_id=s_arc["scene_id"], player_id="A-1101",
                          action="玩家读了巡更记录本", intent="act", scope_id="scp_test_1")
            if unlock["revealed_next"] != s_locked[0] or unlock["mem_kind"] != "unlock":
                err(f"act-turn unlock did not survive the gate: {unlock['revealed_next']!r} / {unlock['mem_kind']!r}")
            if "解锁封存规则 " + s_locked[0] not in unlock["mem_text"]:
                err(f"memory text does not record the act-turn unlock: {unlock['mem_text'][:200]}")
            if s_locked[0] in unlock["narration_redacted"]:
                err("just-unsealed code was scrubbed out of the narration")
            print(f"\n=== host_code[行动轮解锁] locked={s_locked} -> "
                  f"revealed_next={unlock['revealed_next']} kind={unlock['mem_kind']}")

            # 结局条文同样得每轮都攥在主持人手里。行动轮的 top_k 撞不撞得中结局全看措辞，
            # 撞不中时「铁律 5：条件凑齐才给结局」就变成永远不许封盘——达成结局在云端是结构性
            # 不可能。后端按 scene_id 整条交回 scene_endings，这里钉注入、白名单与不重复。
            d_search = json.loads(search_body)
            s_hits = {h.get("code") for h in (d_search.get("hits") or [])}
            s_ends = [e.get("code") for e in (d_search.get("scene_endings") or []) if e.get("code")]
            if not s_ends:
                err("search 响应没有 scene_endings：行动轮主持人拿不到结局触发条件全文")
            s_codes = [c for c in s_arc["codes_csv"].split(",") if c]
            for c in s_ends:
                if c not in s_codes:
                    err(f"结局条文 {c} 没进可引用白名单，铁律 5 永远判不出这一局")
            if len(s_codes) != len(set(s_codes)):
                err(f"命中与整条下发的结局重叠，codes_csv 出现重复编号: {s_codes}")
            if "不得提前向玩家透露" not in s_arc["block"]:
                err("行动轮结局区没有「不得提前透露条件」的约束")
            ending = host(raw=json.dumps({"narration": "你把毛巾挂向海风，潮声停了整整三分钟。",
                                          "triggered": [], "near": [], "sealed_hint": "",
                                          "revealed_now": [], "ending": s_ends[-1], "mood": "calm"},
                                         ensure_ascii=False),
                          codes_csv=s_arc["codes_csv"], locked_csv=s_arc["locked_csv"], revealed_in="",
                          violations_in="", endings_in="", turn=3, scene_title=s_arc["scene_title"],
                          scene_id=s_arc["scene_id"], player_id="A-1101",
                          action="玩家解开盐霜", intent="act", scope_id="scp_test_1")
            if ending["endings_next"] != s_ends[-1] or ending["stage_next"] != "ended":
                err(f"行动轮达成的结局没有封盘: {ending['endings_next']!r} / {ending['stage_next']!r}")
            if ending["mem_kind"] != "ending" or "达成结局 " + s_ends[-1] not in ending["mem_text"]:
                err(f"结局没写进长期记忆: {ending['mem_kind']} / {ending['mem_text'][:160]}")
            print(f"=== host_code[行动轮结局] {s_ends} -> endings_next={ending['endings_next']} "
                  f"stage={ending['stage_next']} kind={ending['mem_kind']}")

            # 「本轮没有命中」这一段以前在 pop 之后才算，等于每轮都把五类全列一遍——
            # 一边把条文交给主持人、一边告诉他这些类别没命中，自相矛盾。现在按实际出现的种类算。
            if "本轮没有命中" in s_arc["block"]:
                err(f"五类俱全的档案块仍报「没有命中」: {s_arc['block'][-260:]!r}")
            only_rule = compiled["archive_code"][0](
                body=json.dumps({"hits": [{"code": "RULE-X-01", "kind": "rule",
                                           "title": "只有一条", "document": "只有规则命中"}],
                                 "channels": {}, "tookMs": 1}, ensure_ascii=False),
                revealed_in="", scene_id_in="scn_x", scene_title_in="X")["block"]
            miss_line = next((l for l in only_rule.splitlines() if l.startswith("## 本轮没有命中")), "")
            if "规则" in miss_line or "结局" not in miss_line:
                err(f"「本轮没有命中」类别算错: {miss_line!r}")

        # 裁决 JSON 被 max_tokens 截断（云端 s13 第 3 轮实测：completion_tokens 2240、
        # 整串 loads 失败、玩家收到兜底文案）时不该把这一局判成空转：叙述要从原文抠回来。
        cut = '{"narration": "潮水退下去的时候，东屋玻璃内侧多了一圈白的。"你把毛巾挂向海风来的方向，窗'
        out = run_host({"narration": ""}, raw=cut)
        if "东屋玻璃" not in out["narration"]:
            err(f"截断的裁决没有救回叙述: {out['narration'][:120]!r}")
        quoted = ('{"narration": "老板“啧”了一声，把拖鞋收走了。", "triggered": ["%s"], '
                  '"near": [], "sealed_hint": "", "revealed_now": [], "ending": "", "mood": "uneasy"}' % good_rule)
        out = run_host({"narration": ""}, raw=quoted)
        if "拖鞋" not in out["narration"] or good_rule not in out["violations_next"]:
            err(f"叙述里的引号把裁决弄丢了: {out['narration'][:120]!r} / {out['violations_next']!r}")
        if good_locked in out["narration"] or "RULE-RAD-05" in out["narration"]:
            err("salvage must not resurrect the model's reasoning text")

        # bad JSON must not explode
        ok_intent = compiled["intent_code"][0](raw='{"intent":"START","keywords":["电梯","对讲机","门"],'
                                                  '"action":"玩家打算走向电梯厅"}', query="换个场景")
        if ok_intent != {"intent": "start", "keywords_csv": "电梯,对讲机,门",
                         "action": "玩家打算走向电梯厅", "reroll": 1, "difficulty": 0}:
            err(f"intent_code normalised wrongly: {ok_intent}")
        if compiled["intent_code"][0](raw='{"intent":"act","keywords":"电梯"}', query="换一个场景试试")["reroll"] != 0:
            err("reroll must be ignored unless intent=start")
        for raw_diff, want in (('{"difficulty":"4"}', 4), ('{"difficulty":9}', 5),
                               ('{"difficulty":"没提"}', 0), ('{"difficulty":null}', 0)):
            got = compiled["intent_code"][0](raw=raw_diff, query="")["difficulty"]
            if got != want:
                err(f"intent_code difficulty {raw_diff} -> {got}, want {want}")

        # 请求体拼装必须扛住玩家原话里的引号/换行/大括号
        hostile = '他说"我要按 13"\n然后 {跑向} 楼梯'
        rq = compiled["req_code"][0](player_id=' A"1/10 1\x01')
        rq2 = compiled["req2_code"][0](query=hostile, action='玩家打算"回头"\n看', keywords_csv="门,声音",
                                       scene_id="scn_office_elevator", revealed="RULE-ELV-06",
                                       reroll=0, top_k=6, difficulty=3, remembered_scene="")
        bodies = {"scope_body": rq["scope_body"], "scene_body": rq2["scene_body"],
                  "search_body": rq2["search_body"]}
        for field, raw_body in bodies.items():
            try:
                parsed = json.loads(raw_body)
            except Exception as exc:  # noqa: BLE001
                err(f"{field} is not valid JSON for hostile input: {exc!r}")
                continue
            if field == "scope_body" and parsed.get("idempotency_key") != "archive-player-A1101":
                err(f"req_code sanitised 工号 wrongly: {parsed}")
            if field == "search_body" and parsed.get("query") != ('玩家打算"回头"\n看 ' + hostile)[:600]:
                err(f"search_body query mangled: {parsed.get('query')!r}")
            if field == "search_body" and parsed.get("scene_id") != "scn_office_elevator":
                err("req2_code dropped the active scene for reroll=0")
            if field == "scene_body":
                if parsed.get("difficulty") != 3:
                    err(f"req2_code scene_body lost the difficulty cap: {parsed}")
                if "玩家打算" in parsed.get("taste", ""):
                    err(f"scene taste polluted by the third-person action summary: {parsed}")
                if "声音" not in parsed.get("taste", ""):
                    err(f"scene taste dropped the player keywords: {parsed}")
        if json.loads(compiled["req2_code"][0](query="换个场景", action="玩家想要新委托",
                                               keywords_csv="门", scene_id="scn_office_elevator",
                                               revealed="", reroll=1, top_k=6, difficulty=0,
                                               remembered_scene="")["scene_body"]) != {
                "taste": "换个场景 门", "scene_id": "", "revealed_csv": "",
                "exclude_scene_id": "scn_office_elevator"}:
            err("reroll=1 should clear scene_id, exclude the old scene and omit difficulty when the player said none")
        bad_archive = compiled["archive_code"][0](body="<html>502</html>", revealed_in="RULE-ELV-06",
                                                 scene_id_in="scn_x", scene_title_in="标题")
        if not isinstance(bad_archive, dict) or bad_archive["scene_id"] != "scn_x":
            err(f"archive_code failure path lost state: {bad_archive}")
        for nid in ("intent_code", "host_code"):
            fn = compiled[nid][0]
            try:
                if nid == "intent_code":
                    r = fn(raw="not json at all", query="继续")
                else:
                    r = fn(raw="###", codes_csv="", locked_csv="", revealed_in="", violations_in="",
                           endings_in="", turn=0, scene_title="", scene_id="", player_id="A-1",
                           action="", intent="act", scope_id="")
                if not isinstance(r, dict):
                    err(f"{nid} malformed-input path returned {type(r).__name__}")
            except Exception as exc:  # noqa: BLE001
                err(f"{nid} crashed on malformed LLM output: {exc!r}")

        # memory failure path（prepare 与 list 同时挂掉也不能让流程断）
        for prep, lst in (('{\'detail\':"boom"}', '{"entries": []}'),
                          ("", "<html>502</html>"),
                          ('{"detail":"boom"}', '{"entries": [{"text": "工号 A-1 在《电梯》第 2 轮 当前场景=scn_office_elevator：违反 RULE-ELV-02"}]}')):
            try:
                r = compiled["memory_code"][0](body=prep, entries_raw=lst)
                if not isinstance(r, dict):
                    err(f"memory_code error path returned {type(r).__name__}")
            except Exception as exc:  # noqa: BLE001
                err(f"memory_code crashed on error body: {exc!r}")
        # list 通道是唯一线索时，也必须把它拼进记忆块并解出场景
        r = compiled["memory_code"][0](body='{"content":""}',
                                      entries_raw='{"entries": [{"text": "工号 A-1 在《电梯》第 2 轮 当前场景=scn_office_elevator：违反 RULE-ELV-02"}]}')
        if r["active_scene_id"] != "scn_office_elevator" or "RULE-ELV-02" not in r["memory_block"]:
            err(f"memory_code ignored the entries/list fallback: {r}")
        # 最新一条写的是 当前场景=none（那轮没派成单）：不能把 none 交给 seekdb，
        # 后端会 404 unknown scene_id none 让整轮红掉，要往回找最近一次真场景。
        r = compiled["memory_code"][0](
            body='{"content":""}',
            entries_raw='{"entries": [{"text": "工号 A-1 在《电梯》第 3 轮 当前场景=scn_office_elevator：平安"}, '
                        '{"text": "工号 A-1 在《午夜档案馆》第 4 轮 当前场景=none：派单失败"}]}')
        if r["active_scene_id"] != "scn_office_elevator":
            err(f"memory_code 没跳过 当前场景=none: {r['active_scene_id']!r}")

    # round-trip proof: the DSL the cloud accepted and re-exported must still match
    # this file where it matters (graph shape + every variable reference).
    export = DSL.parent.parent / "evidence" / "cloud_export" / "app.yaml"
    if export.exists():
        exp = yaml.safe_load(export.read_text(encoding="utf-8"))
        wexp = exp.get("workflow") or exp
        titles_a = {n["data"].get("title") for n in wf["graph"]["nodes"]}
        titles_b = {n["data"].get("title") for n in wexp["graph"]["nodes"]}
        if titles_a != titles_b:
            err(f"cloud export node titles differ: only-local={sorted(titles_a - titles_b)} "
                f"only-export={sorted(titles_b - titles_a)}")
        edges_a = {(e["source"], e["target"]) for e in wf["graph"]["edges"]}
        edges_b = {(e["source"], e["target"]) for e in wexp["graph"]["edges"]}
        if edges_a != edges_b:
            err("cloud export edge set differs from the committed DSL")
        refs_a = {m.group(1) + "." + m.group(2) for m in REF.finditer(json.dumps(wf, ensure_ascii=False))}
        refs_b = {m.group(1) + "." + m.group(2) for m in REF.finditer(json.dumps(wexp, ensure_ascii=False))}
        if refs_a != refs_b:
            err(f"variable references differ: only-local={sorted(refs_a - refs_b)} "
                f"only-export={sorted(refs_b - refs_a)}")
        else:
            print(f"\nround-trip vs cloud export: same {len(edges_a)} edges, same "
                  f"{len(refs_a)} variable references, same node titles")

    print("\n" + "=" * 60)
    for w in warns:
        print("WARN:", w)
    for e in errors:
        print("ERROR:", e)
    print(f"\n{len(errors)} errors, {len(warns)} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

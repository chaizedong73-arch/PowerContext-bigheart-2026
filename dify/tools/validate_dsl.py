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
            out = compiled["memory_code"][0](body=prepare_body)
            if not isinstance(out, dict):
                err(f"memory_code returned {type(out).__name__}, Dify requires dict")
            else:
                if set(out) != {"memory_block", "memory_count"}:
                    err(f"memory_code keys {set(out)} != declared outputs")
                print("memory_code -> count=", out.get("memory_count"))
                print((out.get("memory_block") or "")[:400])

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
            for name, expect in checks:
                if name.startswith("no-"):
                    if expect in blob:
                        err(f"host_code[{label}] kept un-retrieved code {expect!r} ({name})")
                elif expect not in blob:
                    err(f"host_code[{label}] check {name} failed, expected {expect!r} in {blob[:300]}")
            print(f"\n=== host_code[{label}] -> {blob[:330]}")

        # bad JSON must not explode
        ok_intent = compiled["intent_code"][0](raw='{"intent":"START","keywords":["电梯","对讲机","门"],'
                                                 '"action":"玩家打算走向电梯厅","reroll":true}')
        if ok_intent != {"intent": "start", "keywords_csv": "电梯,对讲机,门",
                         "action": "玩家打算走向电梯厅", "reroll": 1, "difficulty": 0}:
            err(f"intent_code normalised wrongly: {ok_intent}")
        if compiled["intent_code"][0](raw='{"intent":"act","keywords":"电梯","reroll":1}')['reroll'] != 0:
            err("reroll must be ignored unless intent=start")
        for raw_diff, want in (('{"difficulty":"4"}', 4), ('{"difficulty":9}', 5),
                               ('{"difficulty":"没提"}', 0), ('{"difficulty":null}', 0)):
            got = compiled["intent_code"][0](raw=raw_diff)["difficulty"]
            if got != want:
                err(f"intent_code difficulty {raw_diff} -> {got}, want {want}")

        # req_code must survive hostile player input (quotes / newlines / braces)
        hostile = '他说"我要按 13"\n然后 {跑向} 楼梯'
        rq = compiled["req_code"][0](player_id=' A"1/10 1\x01', query=hostile, intent="act",
                                     action='玩家打算"回头"\n看', keywords_csv="门,声音",
                                     scene_id="scn_office_elevator", revealed="RULE-ELV-06",
                                     reroll=0, top_k=6, difficulty=3)
        for field in ("scope_body", "scene_body", "search_body"):
            try:
                parsed = json.loads(rq[field])
            except Exception as exc:  # noqa: BLE001
                err(f"req_code.{field} is not valid JSON for hostile input: {exc!r}")
                continue
            if field == "scope_body" and parsed.get("idempotency_key") != "archive-player-A1101":
                err(f"req_code sanitised 工号 wrongly: {parsed}")
            if field == "search_body" and parsed.get("query") != ('玩家打算"回头"\n看 ' + hostile)[:600]:
                err(f"req_code.search_body query mangled: {parsed.get('query')!r}")
            if field == "search_body" and parsed.get("scene_id") != "scn_office_elevator":
                err("req_code dropped the active scene for reroll=0")
            if field == "scene_body":
                if parsed.get("difficulty") != 3:
                    err(f"req_code scene_body lost the difficulty cap: {parsed}")
                if "玩家打算" in parsed.get("taste", ""):
                    err(f"scene taste polluted by the third-person action summary: {parsed}")
                if "声音" not in parsed.get("taste", ""):
                    err(f"scene taste dropped the player keywords: {parsed}")
        if json.loads(compiled["req_code"][0](player_id="A1", query="换个场景", intent="start",
                                              action="玩家想要新委托", keywords_csv="门",
                                              scene_id="scn_office_elevator", revealed="",
                                              reroll=1, top_k=6, difficulty=0)["scene_body"]) != {
                "taste": "换个场景 门", "scene_id": "", "revealed_csv": ""}:
            err("reroll=1 should clear scene_id and omit difficulty when the player said none")
        bad_archive = compiled["archive_code"][0](body="<html>502</html>", revealed_in="RULE-ELV-06",
                                                 scene_id_in="scn_x", scene_title_in="标题")
        if not isinstance(bad_archive, dict) or bad_archive["scene_id"] != "scn_x":
            err(f"archive_code failure path lost state: {bad_archive}")
        for nid in ("intent_code", "host_code"):
            fn = compiled[nid][0]
            try:
                if nid == "intent_code":
                    r = fn(raw="not json at all")
                else:
                    r = fn(raw="###", codes_csv="", locked_csv="", revealed_in="", violations_in="",
                           endings_in="", turn=0, scene_title="", player_id="A-1", action="",
                           intent="act", scope_id="")
                if not isinstance(r, dict):
                    err(f"{nid} malformed-input path returned {type(r).__name__}")
            except Exception as exc:  # noqa: BLE001
                err(f"{nid} crashed on malformed LLM output: {exc!r}")

        # memory failure path
        try:
            r = compiled["memory_code"][0](body='{"detail":"boom"}')
            if not isinstance(r, dict):
                err("memory_code error path returned non-dict")
        except Exception as exc:  # noqa: BLE001
            err(f"memory_code crashed on error body: {exc!r}")

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

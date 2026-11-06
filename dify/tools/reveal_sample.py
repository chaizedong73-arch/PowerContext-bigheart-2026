"""解锁封存规则与达成结局的正向路径取样（可重跑）。

覆盖判题时缺的两条正向样例：
  A. 封存规则 RULE-RAD-05：解锁前取不到 → /archive/reveal 拿到权威原文 →
     解锁后同一查询能取到，且 DSL 的「拼装档案块」「裁决校验与记忆文案」随之放行；
  B. 结局 END-RAD-01：主持人判定达成后，编号进入白名单校验、状态、写回 PowerContext 的记忆文本。

在 WSL 里跑（seekdb 后端 127.0.0.1:8011、PowerContext 127.0.0.1:8000 只在本机可见）：
    cd dify && ~/recallloop-venv/bin/python tools/reveal_sample.py
令牌只从环境变量 ARCHIVE_TOKEN 读，默认是 .env.example 里的占位串，仓库里不落真实凭据。

输出 evidence/reveal_positive_path.txt；任何断言不过就非零退出。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DSL = ROOT / "dsl" / "midnight_archive_chatflow.yml"
EVIDENCE = ROOT / "evidence" / "reveal_positive_path.txt"

ARCH = os.environ.get("ARCHIVE_BASE_URL", "http://127.0.0.1:8011")
PC = os.environ.get("PC_BASE_URL", "http://127.0.0.1:8000")
TOKEN = os.environ.get("ARCHIVE_TOKEN", "change-me-local-dev")

SCENE = "scn_seaside_radio"
SEALED = "RULE-RAD-05"
OTHER_SEALED = "RULE-RAD-06"
ENDING = "END-RAD-01"
RUN = time.strftime("%Y%m%d-%H%M%S")
PLAYER = "A-REVEAL-%s" % RUN

lines: list[str] = []
fails: list[str] = []


def say(text: str = "") -> None:
    lines.append(text)
    print(text)


def check(cond: bool, label: str, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    if not cond:
        fails.append(label)
    say("  [%s] %s%s" % (mark, label, ("  ← " + detail) if detail else ""))


def post(url: str, body: dict, headers: dict | None = None) -> tuple[int, str]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def get(url: str, headers: dict | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url, method="GET", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def arch(path: str, body: dict) -> tuple[int, dict]:
    st, text = post(ARCH + path, body, {"X-Archive-Token": TOKEN})
    try:
        return st, json.loads(text)
    except Exception:
        return st, {"_non_json": text[:400]}


def pc(path: str, body: dict) -> tuple[int, dict]:
    st, text = post(PC + path, body)
    try:
        return st, json.loads(text)
    except Exception:
        return st, {"_non_json": text[:400]}


def codes_of(rows) -> list[str]:
    return [str(r.get("code")) for r in (rows or []) if r.get("code")]


def load_code_node(node_id: str):
    """把 DSL 里那个代码节点的 main() 取出来用——正向样例跑的是提交文件里的同一段代码。"""
    import inspect

    import yaml

    doc = yaml.safe_load(DSL.read_text(encoding="utf-8"))
    for n in doc["workflow"]["graph"]["nodes"]:
        data = n.get("data") or {}
        if n.get("id") == node_id and data.get("code"):
            ns: dict = {}
            exec(compile(data["code"], "<%s>" % node_id, "exec"), ns)
            return ns["main"], inspect.signature(ns["main"]).parameters
    raise SystemExit("node %s not found in DSL" % node_id)


def main() -> int:
    say("# 解锁与结局正向路径取样 · run=%s · 工号=%s" % (RUN, PLAYER))
    say("# ARCHIVE_BASE_URL=%s  PC_BASE_URL=%s  token=%s（占位串，从环境变量读）"
        % (ARCH, PC, "ARCHIVE_TOKEN" if os.environ.get("ARCHIVE_TOKEN") else "change-me-local-dev(默认占位)"))
    say("# 封存目标=%s（场景 %s，另有 %s 保持封存作对照）｜结局目标=%s" % (SEALED, SCENE, OTHER_SEALED, ENDING))
    say("")

    st_h, health_text = get(ARCH + "/archive/health")
    try:
        health = json.loads(health_text)
    except Exception:
        health = {"_non_json": health_text[:400]}
    say("== 0. 后端健康 /archive/health（GET，故意不校验令牌，只回统计数字）HTTP %s" % st_h)
    say(json.dumps(health, ensure_ascii=False, indent=2)[:900])
    check(st_h == 200, "seekdb 档案服务在线（库里 %s 条）" % (health.get("stats") or {}).get("count"))
    say("")

    # ---- A1 解锁前：检索取不到封存条文 ----
    say("== A1. 解锁前 /archive/search（revealed_csv 为空）")
    q = "把点好的歌倒回开头重听一遍，确认她报的是父亲的船号"
    st, s1 = arch("/archive/search", {"query": q, "scene_id": SCENE,
                                      "kinds_csv": "rule,omen,item,npc,ending",
                                      "keywords_csv": "磁带,倒带,点歌,电台", "top_k": 8,
                                      "revealed_csv": ""})
    h1 = codes_of(s1.get("hits"))
    say("  HTTP %s 命中编号：%s" % (st, ",".join(h1) or "（空）"))
    say("  通道：%s" % json.dumps(s1.get("channels"), ensure_ascii=False)[:300])
    check(st == 200, "检索返回 200")
    check(SEALED not in h1, "%s 未解锁时不在 hits 里（条文一个字都没下发）" % SEALED, ",".join(h1))
    say("")

    # ---- A1b 行动轮的解锁候选从哪来 ----
    # 判题说的是「只验了挡得住，没验放得开」。放开的前提是：后端在挡下条文的同时，
    # 把「哪些编号还封着」交回上游，否则行动轮的档案块里 locked_csv 恒为空，
    # 「裁决校验与记忆文案」的 _only(revealed_now, locked) 会把任何解锁都筛掉——
    # 云端 s12/s13 第一版就卡在这里，状态条的「🔓 已解锁封存」永远是空的。
    say("== A1b. 行动轮响应里的 sealed_gated（解锁候选的唯一来源）")
    say("  sealed_gated=%s" % json.dumps(s1.get("sealed_gated"), ensure_ascii=False))
    say("  后端自述：%s" % s1.get("sealed_gated_note"))
    arc_node, _ = load_code_node("archive_code")
    act_blk = arc_node(body=json.dumps(s1, ensure_ascii=False), revealed_in="",
                       scene_id_in=SCENE, scene_title_in="礁石村的深夜电台")
    gated = [c for c in act_blk["locked_csv"].split(",") if c]
    say("  DSL 行动轮档案块 locked_csv=%s｜codes_csv=%s" % (act_blk["locked_csv"], act_blk["codes_csv"]))
    check(SEALED in gated, "被闸门挡下的 %s 以编号形式回到 locked_csv" % SEALED, ",".join(gated))
    check(SEALED not in act_blk["codes_csv"].split(","), "但它的条文没有进入可引用白名单")
    check("封存中" in act_blk["block"] and SEALED + "《" not in act_blk["block"],
          "行动轮档案块列出了封存编号，且不带标题")
    host_node, _ = load_code_node("host_code")

    def act_turn(revealed_now):
        payload = {"narration": "你一把拔掉收音机的插头，在黑里只说了一句「把网收了」。",
                   "triggered": [], "near": [], "ending": "", "sealed_hint": "",
                   "revealed_now": revealed_now, "mood": "uneasy"}
        return host_node(raw=json.dumps(payload, ensure_ascii=False), codes_csv=act_blk["codes_csv"],
                         locked_csv=act_blk["locked_csv"], revealed_in="", violations_in="",
                         endings_in="", turn=2, scene_title=act_blk["scene_title"], scene_id=SCENE,
                         player_id=PLAYER, action="按 ITEM-RAD-01 的用途断电并说话", intent="act",
                         scope_id="scp_sample")

    act_out = act_turn([SEALED])
    act_none = act_turn([])
    say("  本轮玩家做出道具用途动作 → revealed_next=%r kind=%s 引用=%s"
        % (act_out["revealed_next"], act_out["mem_kind"], act_out["cite_line"]))
    say("  同一轮，主持人没给编号        → revealed_next=%r kind=%s"
        % (act_none["revealed_next"], act_none["mem_kind"]))
    check(act_out["revealed_next"] == SEALED and act_out["mem_kind"] == "unlock",
          "行动轮真的放行了解锁（放得开）")
    check(SEALED not in act_out["narration_redacted"], "刚解锁的编号不会被泄露闸门误抹")
    check(act_none["revealed_next"] == "" and act_none["mem_kind"] == "progress",
          "没有编号就不记账——白名单不是走过场")
    say("")

    # ---- A1c 行动轮也要攥得住结局条文 ----
    # 结局不封存，但挤不挤得进 top_k 全看措辞：撞不中时主持人手里没有触发条件全文，
    # 铁律 5「条件凑齐才给结局」就变成永远不许封盘。后端按 scene_id 整条交回 scene_endings。
    say("== A1c. 行动轮响应里的 scene_endings（结局触发条件的唯一稳定来源）")
    ends_sent = [e.get("code") for e in (s1.get("scene_endings") or [])]
    say("  后端交回结局：%s" % ",".join([c for c in ends_sent if c]))
    say("  后端自述：%s" % s1.get("scene_endings_note"))
    check(bool(ends_sent), "带 scene_id 的检索一定附上本场景结局条文")
    for c in ends_sent:
        check(c in act_blk["codes_csv"].split(","), "%s 进入行动轮可引用白名单" % c)
    check(len([c for c in act_blk["codes_csv"].split(",") if c])
          == len(set([c for c in act_blk["codes_csv"].split(",") if c])),
          "命中条目与整条下发的结局不重复计")
    miss = next((l for l in act_blk["block"].splitlines() if l.startswith("## 本轮没有命中")), "")
    contradict = [k for k in ("规则", "预兆", "道具", "NPC", "结局")
                  if ("可引用的" + k) in act_blk["block"] and miss and k in miss]
    say("  「本轮没有命中」行：%s" % (miss or "（没有这一行）"))
    check(not contradict, "档案块不会一边下发「可引用的%s」一边说这一类没命中" % "、".join(contradict))

    good_end = next((c for c in ends_sent if c == "END-RAD-03"), ends_sent[-1] if ends_sent else "")
    end_turn = host_node(raw=json.dumps({"narration": "潮声停了整整三分钟，玻璃内侧露出外面的黑。",
                                         "triggered": [], "near": [], "ending": good_end,
                                         "sealed_hint": "", "revealed_now": [], "mood": "释然"},
                                        ensure_ascii=False),
                         codes_csv=act_blk["codes_csv"], locked_csv=act_blk["locked_csv"],
                         revealed_in="", violations_in="", endings_in="", turn=3,
                         scene_title=act_blk["scene_title"], scene_id=SCENE, player_id=PLAYER,
                         action="煤油三分钟里擦净盐霜并挂出毛巾", intent="act", scope_id="scp_sample")
    say("  行动轮判定达成 %s → endings_next=%r stage_next=%s kind=%s"
        % (good_end, end_turn["endings_next"], end_turn["stage_next"], end_turn["mem_kind"]))
    check(end_turn["endings_next"] == good_end and end_turn["stage_next"] == "ended",
          "行动轮（不是派单轮）也能真的封盘")
    check("达成结局 " + good_end in end_turn["mem_text"], "结局写进记忆文本")
    say("")

    # ---- A2 解锁前：派单包只给编号+危险度 ----
    say("== A2. 解锁前 /archive/scene_pack（revealed_csv 为空）")
    st, p1 = arch("/archive/scene_pack", {"taste": "", "scene_id": SCENE, "revealed_csv": ""})
    locked1 = [l.get("code") for l in (p1.get("sealed_locked") or [])]
    rules1 = codes_of((p1.get("pack") or {}).get("rule"))
    say("  HTTP %s 可读规则：%s" % (st, ",".join(rules1)))
    say("  封存中（只有编号与危险度）：%s" % json.dumps(p1.get("sealed_locked"), ensure_ascii=False))
    check(SEALED in locked1 and SEALED not in rules1, "%s 在 sealed_locked、不在 pack.rule" % SEALED)
    blk1 = arc_node(body=json.dumps(p1, ensure_ascii=False), revealed_in="", scene_id_in="", scene_title_in="")
    seal_line = next((l for l in blk1["block"].splitlines() if l.startswith("## 封存中")), "")
    say("  DSL「拼装档案块」渲染出的封存行：%s" % seal_line[:200])
    check(SEALED in seal_line, "档案块里只有封存编号，没有条文")
    say("")

    # ---- A3 reveal 拿到权威原文 ----
    say("== A3. /archive/reveal 解锁 %s" % SEALED)
    st, rv = arch("/archive/reveal", {"scene_id": SCENE, "code": SEALED,
                                      "reason": "玩家在上一轮从渔姑处拿到 1998 年点歌单（正向路径取样）"})
    rule = rv.get("rule") or {}
    say("  HTTP %s engine=%s" % (st, rv.get("engine")))
    say("  权威原文：[%s] %s" % (rule.get("code"), rule.get("title")))
    say("  %s" % (rule.get("document") or "")[:600])
    check(st == 200 and rule.get("code") == SEALED, "reveal 返回该封存条文的原文")
    check(bool(rule.get("sealed")), "该条目在库里确实标着 sealed=true", str(rule.get("sealed")))
    st_bad, _ = arch("/archive/reveal", {"scene_id": SCENE, "code": "RULE-NOT-EXIST-99", "reason": "负向对照"})
    check(st_bad == 404, "不存在的编号 reveal 返回 404（不是静默放行）", "HTTP %s" % st_bad)
    say("")

    # ---- A4 解锁后：同一查询取到了 ----
    say("== A4. 解锁后 /archive/search（revealed_csv=%s）" % SEALED)
    st, s2 = arch("/archive/search", {"query": q, "scene_id": SCENE,
                                      "kinds_csv": "rule,omen,item,npc,ending",
                                      "keywords_csv": "磁带,倒带,点歌,电台", "top_k": 8,
                                      "revealed_csv": SEALED})
    h2 = codes_of(s2.get("hits"))
    say("  HTTP %s 命中编号：%s" % (st, ",".join(h2)))
    check(SEALED in h2, "同一句话、同一个库，只因为传了解锁状态就把条文捞回来了", ",".join(h2))
    said = next((r for r in (s2.get("hits") or []) if r.get("code") == SEALED), {})
    say("  命中条目：rank=%s kind=%s score=%s" % (said.get("rank"), said.get("kind"), said.get("score")))
    check(OTHER_SEALED not in h2, "%s 仍被封存（解锁是逐条的，不是一键全放）" % OTHER_SEALED)
    say("")

    # ---- A5 解锁后：派单包放行 ----
    say("== A5. 解锁后 /archive/scene_pack（revealed_csv=%s）" % SEALED)
    st, p2 = arch("/archive/scene_pack", {"taste": "", "scene_id": SCENE, "revealed_csv": SEALED})
    locked2 = [l.get("code") for l in (p2.get("sealed_locked") or [])]
    rules2 = codes_of((p2.get("pack") or {}).get("rule"))
    blk2 = arc_node(body=json.dumps(p2, ensure_ascii=False), revealed_in=SEALED,
                    scene_id_in="", scene_title_in="")
    say("  可读规则：%s" % ",".join(rules2))
    say("  仍封存：%s" % ",".join(locked2))
    check(SEALED in rules2 and SEALED not in locked2, "档案包把 %s 的全文放进可引用区" % SEALED)
    check(OTHER_SEALED in locked2, "%s 仍在 sealed_locked" % OTHER_SEALED)
    in_block = SEALED in blk2["block"]
    say("  档案块里该编号出现：%s｜codes_csv 含它：%s" % (in_block, SEALED in blk2["codes_csv"].split(",")))
    check(in_block and SEALED in blk2["codes_csv"].split(","), "解锁后编号进入主持人可引用白名单")
    say("")

    # ---- A6 叙述闸门：同一段话，解锁前后待遇不同 ----
    say("== A6. DSL「裁决校验与记忆文案」的泄露闸门（同一句叙述，解锁前/后各跑一次）")
    host = host_node  # A1b 已经把提交文件里那段节点代码 exec 过一次了
    narration = ("你把磁带倒回开头。%s 说得很清楚：三秒空白里出声的人，当晚不许再听电台。" % SEALED)
    ans = {"narration": narration, "triggered": [SEALED], "near": [], "ending": "",
           "sealed_hint": OTHER_SEALED, "revealed_now": [], "mood": "阴冷"}

    def run_host(**over):
        kw = dict(raw=json.dumps(ans, ensure_ascii=False), codes_csv=blk1["codes_csv"],
                  locked_csv=blk1["locked_csv"], revealed_in="", violations_in="", endings_in="",
                  turn=4, scene_title=blk1["scene_title"] or SCENE, scene_id=SCENE,
                  player_id=PLAYER, action="玩家把磁带倒回开头重听", intent="act", scope_id="scp_sample")
        kw.update(over)
        return host(**kw)

    before = run_host()
    after = run_host(codes_csv=blk2["codes_csv"], locked_csv=blk2["locked_csv"], revealed_in=SEALED)
    say("  解锁前 narration：%s" % before["narration"][:160])
    say("           抹掉的编号：%r  引用：%s" % (before["narration_redacted"], before["cite_line"]))
    say("  解锁后 narration：%s" % after["narration"][:160])
    say("           抹掉的编号：%r  引用：%s" % (after["narration_redacted"], after["cite_line"]))
    check(SEALED in before["narration_redacted"] and SEALED not in before["narration"],
          "未解锁时编号被换成「一段还没被写下来的规矩」")
    check(after["narration_redacted"] == "" and SEALED in after["narration"],
          "已解锁时同一句话原样保留，闸门不误伤")
    check(after["violations_next"] == SEALED, "裁决照常记账：violations_next=%s" % after["violations_next"])
    check(before["sealed_hint"] == OTHER_SEALED and after["sealed_hint"] == OTHER_SEALED,
          "sealed_hint 只允许指向仍封存的编号（%s）" % OTHER_SEALED)
    check(OTHER_SEALED not in after["narration"], "对照封存编号没有出现在可引用区")
    say("")

    # ---- B 结局正向路径 ----
    say("== B. 结局 %s 的正向路径" % ENDING)
    endings = codes_of((p2.get("pack") or {}).get("ending"))
    say("  本场景可引用结局编号：%s" % ",".join(endings))
    check(ENDING in endings, "%s 在档案包 ending 区（archive_code 已把它并入 codes_csv）" % ENDING)
    ans_end = {"narration": "鸡叫之前你把收音机搬到了门厅。她报的最后一条歌名，是给你父亲留的位置。",
               "triggered": [], "near": [], "ending": ENDING, "sealed_hint": "",
               "revealed_now": [OTHER_SEALED], "mood": "释然"}
    end_out = host(raw=json.dumps(ans_end, ensure_ascii=False), codes_csv=blk2["codes_csv"],
                   locked_csv=blk2["locked_csv"], revealed_in=SEALED, violations_in=SEALED,
                   endings_in="", turn=5, scene_title=blk2["scene_title"], scene_id=SCENE,
                   player_id=PLAYER, action="抱着收音机在门厅等到鸡叫", intent="act", scope_id="scp_sample")
    say("  mem_kind=%s stage_next=%s" % (end_out["mem_kind"], end_out["stage_next"]))
    say("  violations_next=%r endings_next=%r revealed_next=%r"
        % (end_out["violations_next"], end_out["endings_next"], end_out["revealed_next"]))
    say("  写回记忆：%s" % end_out["mem_text"])
    check(end_out["stage_next"] == "ended", "达成结局后状态机进入 ended")
    check(end_out["endings_next"] == ENDING, "结局编号进入累计状态")
    check(OTHER_SEALED in end_out["revealed_next"], "本轮新解锁的 %s 并入 revealed_next" % OTHER_SEALED)
    check(ENDING in end_out["mem_text"] and "达成结局" in end_out["mem_text"], "结局写进了长期记忆的文本")
    check("当前场景=%s" % SCENE in end_out["mem_text"], "记忆文本带 当前场景= 标签（跨会话续局靠它）")
    check(end_out["mem_kind"] == "unlock",
          "本轮没有新违规、但有新解锁 → kind=unlock（即便同时达成结局）")

    def host_turn(triggered, revealed_now, ending):
        payload = {"narration": "测试轮。", "triggered": triggered, "near": [], "ending": ending,
                   "sealed_hint": "", "revealed_now": revealed_now, "mood": ""}
        return host(raw=json.dumps(payload, ensure_ascii=False), codes_csv=blk2["codes_csv"],
                    locked_csv=blk2["locked_csv"], revealed_in=SEALED, violations_in="", endings_in="",
                    turn=6, scene_title=blk2["scene_title"], scene_id=SCENE, player_id=PLAYER,
                    action="优先级取样", intent="act", scope_id="scp_sample")

    say("  记忆 kind 的优先级（同一节点，四种本轮事实）：")
    for label, got, want in (
        ("违规+解锁+结局", host_turn([SEALED], [OTHER_SEALED], ENDING)["mem_kind"], "violation"),
        ("解锁+结局", host_turn([], [OTHER_SEALED], ENDING)["mem_kind"], "unlock"),
        ("只有结局", host_turn([], [], ENDING)["mem_kind"], "ending"),
        ("什么都没有", host_turn([], [], "")["mem_kind"], "progress"),
    ):
        say("    %-14s -> %s" % (label, got))
        check(got == want, "kind 优先级 violation>unlock>ending>progress：%s 取 %s" % (label, want))
    halluc = host(raw=json.dumps({"narration": "你完成了《未写出来的结局》。", "ending": "END-XXX-99"},
                                 ensure_ascii=False), codes_csv=blk2["codes_csv"],
                  locked_csv=blk2["locked_csv"], revealed_in=SEALED, violations_in="", endings_in="",
                  turn=5, scene_title=blk2["scene_title"], scene_id=SCENE, player_id=PLAYER,
                  action="随便试", intent="act", scope_id="scp_sample")
    check(halluc["endings_next"] == "" and halluc["stage_next"] == "playing",
          "模型编造不在白名单里的结局编号时不记账、不封盘")
    say("")

    # ---- C 真实写回 PowerContext 并读回 ----
    say("== C. 把这两轮事实真写回 PowerContext，再按两条通道读回")
    st, sc = pc("/v1/scopes", {"title": "午夜档案馆·工号%s" % PLAYER, "summary": "解锁正向路径取样",
                               "idempotency_key": "archive-player-%s" % PLAYER})
    scope_id = sc.get("scope_id", "")
    say("  HTTP %s scope_id=%s" % (st, scope_id))
    check(st in (200, 201) and bool(scope_id), "建档成功")
    for body_name, payload in (("A6 解锁后的违规轮", after["remember_body"]), ("B 达成结局那一轮", end_out["remember_body"])):
        j = json.loads(payload)
        j["scope_id"] = scope_id
        st2, _ = pc("/v1/memory/remember", j)
        say("  remember(%s) HTTP %s kind=%s" % (body_name, st2, j.get("kind")))
        check(st2 in (200, 201), "写入 %s 被接受" % j.get("kind"))
    st3, prep = pc("/v1/context/prepare", {"scope_id": scope_id,
                                           "query": "%s 电台,磁带,倒带 违反 违规 解锁 封存 结局 预兆" % PLAYER,
                                           "max_bytes": 6000})
    st4, lst = pc("/v1/memory/entries/list", {"scope_id": scope_id, "include_inactive": False})
    mem_node, _ = load_code_node("memory_code")
    merged = mem_node(body=json.dumps(prep, ensure_ascii=False), entries_raw=json.dumps(lst, ensure_ascii=False))
    say("  prepare HTTP %s｜entries/list HTTP %s（写回 2 条）" % (st3, st4))
    say("  DSL「解析记忆条目」合并后 memory_count=%s active_scene_id=%s"
        % (merged["memory_count"], merged["active_scene_id"]))
    say("  记忆块：\n%s" % merged["memory_block"])
    check(merged["memory_count"] >= 2, "两条都读回来了（两路并读、去重）")
    check(merged["active_scene_id"] == SCENE, "从记忆标签解出当前场景=%s，新会话也能续上这一局" % SCENE)
    check(SEALED in merged["memory_block"] or ENDING in merged["memory_block"],
          "解锁/结局事实出现在主持人看到的旧账块里")
    say("")

    say("== D. 这条样例证明了什么、没证明什么")
    say("  证明：封存条文的放行完全由后端与状态驱动——解锁前后是同一个库、同一句话、同一个查询；")
    say("        reveal 只在被调用时返回原文，不存在的编号是 404；")
    say("        DSL 的白名单闸门对「未解锁编号出现在叙述里」做字符串级抹除，对已解锁编号不误伤；")
    say("        结局编号进入状态机与长期记忆，编造的编号不记账；记忆文本带 当前场景= 标签。")
    say("  没证明：这些是后端 + DSL 代码节点层面的取样，不是云端 Dify 一次真实运行的记录；")
    say("        云端运行证据（workflow 日志、节点记录、截图）见 evidence/ 下的线上文件，需与 §4 重导入后的 DSL 对齐。")
    say("")
    say("断言：%d 通过，%d 失败%s" % (
        sum(1 for l in lines if "[PASS]" in l), len(fails),
        "" if not fails else " → " + "; ".join(fails)))
    EVIDENCE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\nwrote %s" % EVIDENCE)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())

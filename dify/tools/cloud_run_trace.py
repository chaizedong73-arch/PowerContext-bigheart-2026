"""云端运行取证：跑一次真实的 Dify 应用，把 workflow_run_id 与逐节点记录落进 evidence/。

判题指出的缺口是「运行证据回溯不到提交文件，且没有 workflow_run_id / 节点记录」。
App API 的 blocking 响应里没有运行 ID，但 **streaming（SSE）** 里有：
  workflow_started  → 运行 ID、workflow_id
  node_started / node_finished → 每个节点的标题、类型、状态、耗时、输出
  message / message_end → 主持人回复与 token 用量

所以这个脚本用 response_mode=streaming 逐轮调用 /chat-messages，把上面这些事件按顺序整理成
一份纯文本证据，文件头写上「本次跑的是哪个 commit、DSL 的 sha256」，让运行记录能对上提交文件。

密钥只从环境变量读，绝不写进仓库：
    export DIFY_API_KEY=...            # 控制台「访问 API」里新建，用完可撤销
    export DIFY_BASE_URL=https://api.dify.ai/v1
    cd dify && python3 tools/cloud_run_trace.py s3_same_conversation
    # 离线自测解析器（不联网）：
    python3 tools/cloud_run_trace.py --replay capture/sse_sample.txt somename

每个场景 = 若干轮 (query, 是否新会话, 工号)。回复里的 conversation_id 会被带走下一轮，
「换个会话、同工号」那种旧账场景靠显式重置实现。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence"
DSL = ROOT / "dsl" / "midnight_archive_chatflow.yml"


def dsl_node_count() -> int:
    """提交的 DSL 里有多少节点（用来解释云端为什么只回 19 个 node_finished）。"""
    try:
        import yaml
        doc = yaml.safe_load(DSL.read_text(encoding="utf-8"))
        return len(doc["workflow"]["graph"]["nodes"])
    except Exception:
        return 0


NODES_IN_DSL = dsl_node_count()

# 这几个节点的输出是判题缺口直接问的东西，正常也打出来：
#   组装检索请求体 → 换单时有没有把上一幕交给后端排除、续局时 scene_id 从哪来；
#   取场景档案包  → 后端实际选了哪一幕、有没有回显 reroll_excluded_scene_id。
SHOW_OUTPUT = ("组装检索请求体", "取场景档案包")

# 节点输出是一整坨转义过的 JSON，截断后看不见最关键的那几个字段，所以单独抽出来。
SPOT_FIELDS = (("上一幕是否交给后端排除", r'"exclude_scene_id": *"[^"]*"'),
               ("后端回显的排除幕", r'"reroll_excluded_scene_id": *"[^"]*"'),
               ("本轮认定的场景", r'"scene_id": *"[^"]*"'),
               ("派单口味", r'"taste": *"[^"]*"'))

SCENARIOS: dict[str, list[dict]] = {
    # 同会话两轮：违规必须兑现书面后果（对应 VALIDATION S3）
    "s3_same_conversation": [
        {"new_conversation": True, "player": "A-CLOUD-S3",
         "query": "今晚给我派个活，我想要有声音、后背发凉但别太吓人的。"},
        {"new_conversation": False, "player": "A-CLOUD-S3",
         "query": "我把音量旋钮从 3 往下拧到 1，然后开口跟着她的调子哼了两个音。"},
    ],
    # 换单词面 vs「继续」：同一个会话里先续后换（对应判题说的「继续被重派」）
    "s10_reroll_words": [
        {"new_conversation": True, "player": "A-CLOUD-S10",
         "query": "给我派个跟声音有关的活，中等难度。"},
        {"new_conversation": False, "player": "A-CLOUD-S10", "query": "继续，别换活。"},
        {"new_conversation": False, "player": "A-CLOUD-S10", "query": "接着讲上一局。"},
        {"new_conversation": False, "player": "A-CLOUD-S10", "query": "换个场景，重新派单吧。"},
    ],
    # 跨会话同工号：旧账与续局（对应 VALIDATION S4 / S9-C）
    "s11_cross_conversation": [
        {"new_conversation": True, "player": "A-CLOUD-S11",
         "query": "今晚给我派个活，海边的、有电台那种。"},
        {"new_conversation": False, "player": "A-CLOUD-S11",
         "query": "我在她报完歌名的三秒空白里哼了一句。"},
        {"new_conversation": True, "player": "A-CLOUD-S11",
         "query": "继续上一局，别重派。"},
    ],
    # 解锁正向路径：说出条件 → 主持人调 reveal → 原文进入可引用（对应 VALIDATION S9）
    "s12_unlock_positive": [
        {"new_conversation": True, "player": "A-CLOUD-S12",
         "query": "给我派海边那个有电台的活，我要查我父亲那条船。"},
        {"new_conversation": False, "player": "A-CLOUD-S12",
         "query": "电台报出的船号和我父亲那艘一样，我立刻拔掉插头断电，在黑里只说一句「把网收了」。"},
    ],
    # 云端解锁/结局正向路径：判题说「只验了挡得住，没验放得开」。这一场景把玩家动作
    # 说到道具编号上（铁律 4 要的就是这个），看主持人会不会真的放行 revealed_now / ending。
    "s13_unlock_cloud": [
        {"new_conversation": True, "player": "A-CLOUD-S13",
         "query": "给我派海边那个有电台的活，中等难度。"},
        {"new_conversation": False, "player": "A-CLOUD-S13",
         "query": "我先摊开防潮袋里的排班表（ITEM-RAD-02）核对电台刚报的船号，确认就是我父亲那一艘；"
                  "接着用铁皮烟盒里的备用插头（ITEM-RAD-01）一把拔掉收音机的插头断电，"
                  "在黑里只说一句「把网收了」。"},
        {"new_conversation": False, "player": "A-CLOUD-S13",
         "query": "我把灶房窗台那半罐煤油（ITEM-RAD-03）浸湿布条，点在门槛内侧，"
                  "趁火苗转蓝、潮声停住的三分钟用干毛巾擦净东屋玻璃内侧的盐霜，"
                  "再把毛巾挂向海风来的方向。"},
    ],
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_lf(path: Path) -> str:
    """同一份文件把 CRLF 归一成 LF 之后的哈希。

    工作树在 Windows 上，文件是 CRLF；git 提交时按 LF 存 blob（core.autocrlf=true），
    所以「git show HEAD:dify/dsl/… | sha256sum」拿到的是这个值，而不是上面那个。
    两个都写下来，核对的人才不会因为换行符以为证据对不上。
    """
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def git(*args: str) -> str | None:
    """git 信息优先由调用方从 Windows 侧用环境变量传进来。

    原因：这个工作树是 git worktree，它的 `.git` 指针写的是 Windows 路径
    （`gitdir: C:/Users/.../.git/worktrees/...`），在 WSL 里直接跑 git 会打不开，
    静默返回空串就会把「拿不到」误报成「工作树干净」。
    """
    env = {"rev-parse --abbrev-ref HEAD": "TRACE_BRANCH",
           "rev-parse HEAD": "TRACE_HEAD",
           "status --short": "TRACE_STATUS"}.get(" ".join(args))
    if env and os.environ.get(env):
        return os.environ[env].strip()
    try:
        out = subprocess.run(["git", *args], cwd=str(ROOT.parent), capture_output=True, text=True, timeout=10)
        if out.returncode != 0:
            return None
        return (out.stdout or "").strip()
    except Exception:
        return None


def parse_sse(text: str) -> list[dict]:
    """把 SSE 正文解析成事件列表；一行 data: {...} 一条。"""
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            events.append(json.loads(payload))
        except Exception:
            events.append({"event": "_unparsable", "raw": payload[:300]})
    return events


def summarize(events: list[dict]) -> dict:
    out = {"workflow_run_id": "", "workflow_id": "", "message_id": "", "conversation_id": "",
           "answer_parts": [], "nodes": [], "usage": None, "errors": [], "task_id": ""}
    for e in events:
        ev = e.get("event")
        if ev == "workflow_started":
            d = e.get("data") or {}
            out["workflow_run_id"] = d.get("id") or out["workflow_run_id"]
            out["workflow_id"] = d.get("workflow_id") or out["workflow_id"]
        elif ev == "node_started":
            d = e.get("data") or {}
            out["nodes"].append({"kind": "started", "title": d.get("title"), "type": d.get("node_type"),
                                 "node_id": d.get("node_id"), "status": "running", "elapsed": None})
        elif ev == "node_finished":
            d = e.get("data") or {}
            out["nodes"].append({"kind": "finished", "title": d.get("title"), "type": d.get("node_type"),
                                 "node_id": d.get("node_id"), "status": d.get("status"),
                                 "elapsed": d.get("elapsed_time"),
                                 "outputs": _trim(d.get("outputs")),
                                 "outputs_raw": json.dumps(d.get("outputs"), ensure_ascii=False),
                                 "error": d.get("error")})
        elif ev == "workflow_finished":
            d = e.get("data") or {}
            out["workflow_status"] = d.get("status")
            out["workflow_total_elapsed"] = d.get("total_elapsed_time")
            out["workflow_outputs"] = _trim(d.get("outputs"))
        elif ev in ("message", "message_end"):
            if e.get("answer"):
                out["answer_parts"].append(e["answer"])
            if e.get("message_id"):
                out["message_id"] = e["message_id"]
            if e.get("conversation_id"):
                out["conversation_id"] = e["conversation_id"]
            # blocking 里 token 用量在 data，streaming 的 message_end 放在 metadata.usage
            md = e.get("metadata") or {}
            if md.get("usage"):
                out["usage"] = md["usage"]
            if e.get("data"):
                out["usage"] = e["data"]
        elif ev in ("error",):
            out["errors"].append(json.dumps(e, ensure_ascii=False)[:400])
        if e.get("task_id"):
            out["task_id"] = e["task_id"]
    out["answer"] = "".join(out["answer_parts"])
    return out


def _trim(obj, limit: int = 700) -> str:
    if obj is None:
        return ""
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    text = re.sub(r"\s*\n\s*", " ⏎ ", text)
    return text[:limit] + ("…" if len(text) > limit else "")


def call_stream(base: str, key: str, body: dict) -> str:
    req = urllib.request.Request(
        base.rstrip("/") + "/chat-messages",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    chunks = []
    with urllib.request.urlopen(req, timeout=300) as r:
        for raw in r:
            chunks.append(raw.decode("utf-8", "replace"))
    return "".join(chunks)


def render(name: str, turns: list[dict]) -> tuple[list[str], bool]:
    base = os.environ.get("DIFY_BASE_URL", "https://api.dify.ai/v1")
    key = os.environ.get("DIFY_API_KEY", "")
    lines: list[str] = []
    ok = True
    lines.append("# 云端运行取证 · 场景 %s · 取样时间 %s" % (name, time.strftime("%Y-%m-%dT%H:%M:%S%z")))
    lines.append("# 可回溯性：本次跑的 DSL 文件与其哈希")
    lines.append("#   DSL  = dify/%s" % DSL.relative_to(ROOT).as_posix())
    lines.append("#   sha256(dsl) = %s" % sha256(DSL))
    lines.append("#   sha256(dsl，换行归一成 LF) = %s  ← 仓库 blob 存的是 LF，"
                 "用 `git show HEAD:dify/dsl/midnight_archive_chatflow.yml | sha256sum` 得到的是这个" % sha256_lf(DSL))
    head = git("rev-parse", "HEAD")
    lines.append("#   git HEAD = %s" % (head or "(拿不到：请用 Windows 侧 git 结果传 TRACE_HEAD)"))
    status = git("status", "--short")
    lines.append("#   git status --short（取样时未提交的改动）= %s"
                 % ("(拿不到)" if status is None else (status.replace("\n", " | ") or "干净")))
    lines.append("#   分支 = %s" % (git("rev-parse", "--abbrev-ref", "HEAD") or "(拿不到)"))
    lines.append("# 调用方式：POST %s/chat-messages，response_mode=streaming（blocking 响应里没有运行 ID）" % base)
    lines.append("# 密钥不落文件：只用环境变量 DIFY_API_KEY，这里只记后 4 位 = %s"
                 % (("…" + key[-4:]) if key else "(未设置)"))
    if not key:
        lines.append("")
        lines.append("!! 未设置 DIFY_API_KEY，没有发起任何调用。")
        return lines, False
    conversation = ""
    for i, t in enumerate(turns, 1):
        if t.get("new_conversation"):
            conversation = ""
        body = {"inputs": {"player_id": t["player"]}, "query": t["query"],
                "response_mode": "streaming", "user": "cloud-trace-%s" % name}
        if conversation:
            body["conversation_id"] = conversation
        lines.append("")
        lines.append("== 第 %d 轮  工号=%s  会话=%s" % (i, t["player"], "新开会话" if not conversation else conversation))
        lines.append("   玩家原话：%s" % t["query"])
        t0 = time.time()
        try:
            raw = call_stream(base, key, body)
        except Exception as exc:
            lines.append("   !! 调用失败：%s" % exc)
            ok = False
            continue
        s = summarize(parse_sse(raw))
        conversation = s["conversation_id"] or conversation
        lines.append("   workflow_run_id = %s   (workflow_id=%s)" % (s["workflow_run_id"], s["workflow_id"]))
        lines.append("   message_id = %s   conversation_id = %s   task_id = %s"
                     % (s["message_id"], s["conversation_id"], s["task_id"]))
        finished = [n for n in s["nodes"] if n["kind"] == "finished"]
        lines.append("   运行状态 = %s   本地整轮计时 = %.1f s"
                     % (s.get("workflow_status"), time.time() - t0))
        if s["errors"]:
            ok = False
            for e in s["errors"]:
                lines.append("   !! 事件 error：%s" % e)
        lines.append("   节点记录（node_finished 事件 %d 条，另有 node_started %d 条；"
                     "if-else 二分叉，本轮只走 %s 那一侧，所以执行的是 %d 个节点（DSL 共 %s 个））："
                     % (len(finished), len(s["nodes"]) - len(finished),
                        "取场景档案包" if any("取场景档案包" in (n["title"] or "") for n in finished) else "检索相关档案",
                        len(finished), NODES_IN_DSL or "?"))
        for n in finished:
            flag = "ok " if n["status"] == "succeeded" else "!! "
            lines.append("     %s%-14s %-22s status=%-10s %-6ss" % (flag, n["type"], n["title"], n["status"], n["elapsed"]))
            if n["status"] != "succeeded":
                ok = False
                lines.append("        error=%s outputs=%s" % (n["error"], n["outputs"]))
            elif any(k in (n["title"] or "") for k in SHOW_OUTPUT):
                lines.append("        outputs=%s" % n["outputs"])
                flat = (n.get("outputs_raw") or "").replace('\\"', '"')
                for label, pat in SPOT_FIELDS:
                    found = list(dict.fromkeys(re.findall(pat, flat)))
                    lines.append("        %s：%s" % (label, " / ".join(found[:3]) or "(本轮没有这个字段)"))
        lines.append("   usage = %s" % _trim(s["usage"], 300))
        lines.append("   主持人回复：")
        for ln in (s["answer"] or "（空）").splitlines():
            lines.append("     | " + ln)
        lines.append("   本轮工作流输出 = %s" % _trim(s.get("workflow_outputs"), 1000))
    return lines, ok


def replay(path: Path, name: str) -> tuple[list[str], bool]:
    """离线自测：把一段真实 SSE 文本喂给解析器，确认能抽出运行 ID 与节点记录。"""
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"\n\s*\n", "\n".join(text.splitlines()))
    lines = ["# 解析器离线自测（--replay %s）" % path.name, "# 场景名 %s" % name]
    ok = False
    for b in blocks:
        if not b.strip():
            continue
        s = summarize(parse_sse(b))
        if s["workflow_run_id"]:
            ok = True
        lines.append("  workflow_run_id=%s nodes=%d answer_len=%d conv=%s"
                     % (s["workflow_run_id"], len([n for n in s["nodes"] if n["kind"] == "finished"]),
                        len(s["answer"]), s["conversation_id"]))
        for n in s["nodes"]:
            if n["kind"] == "finished":
                lines.append("    - %s/%s status=%s %ss" % (n["type"], n["title"], n["status"], n["elapsed"]))
    lines.append("结论：%s" % ("能抽出 workflow_run_id 与逐节点记录" if ok else "没抽到运行 ID，解析器或样本有问题"))
    return lines, ok


def main() -> int:
    args = [a for a in sys.argv[1:]]
    if args and args[0] == "--replay":
        sample = Path(args[1])
        name = args[2] if len(args) > 2 else "replay"
        lines, ok = replay(sample, name)
        print("\n".join(lines))
        return 0 if ok else 1
    name = args[0] if args else "s3_same_conversation"
    turns = SCENARIOS.get(name)
    if not turns:
        print("未知场景 %r，可选：%s" % (name, ", ".join(sorted(SCENARIOS))))
        return 2
    lines, ok = render(name, turns)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE / ("cloud_run_%s.txt" % name)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote %s  (all_nodes_ok=%s)" % (out, ok))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

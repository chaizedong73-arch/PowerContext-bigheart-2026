"""换单必须真的换一幕：对着 live seekdb 后端做「同一句口味、只差一个排除参数」的对照。

为什么要这条证据：玩家说「换个场景」时没有口味词，而 DSL 会把意图关键词一起拼进口味里
（「深夜 电台 海边 声音」），向量最近的那一幕就还是刚才那一幕——云端实测踩过。
后端 ScenePackBody 因此多了 exclude_scene_id；本脚本把「不排除 / 排除当前幕」各跑一遍，
两次调用之间只有这一个参数不同，结果差在哪一眼就能看出来。

令牌仍只从环境变量读，仓库不落真实凭据：
    export ARCHIVE_URL=http://127.0.0.1:8011
    export ARCHIVE_TOKEN=<后端启动时用的那一个>
    cd dify && python3 tools/reroll_exclude_probe.py
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "evidence" / "backend_reroll_exclude.txt"
BASE = os.environ.get("ARCHIVE_URL", "http://127.0.0.1:8011").rstrip("/")
TOKEN = os.environ.get("ARCHIVE_TOKEN", "change-me-local-dev")

CURRENT = "scn_seaside_radio"
# 三句口味：A 是玩家原话，B 是 DSL 实际会发出去的（原话 + 意图关键词），C 是换单但什么口味都没给
TASTES = (
    ("A 玩家原话", "换个场景，重新派单吧。"),
    ("B DSL 实际发的（原话+意图关键词）", "换个场景，重新派单吧。 深夜 电台 海边 声音"),
    ("C 换单但玩家没给口味词", "换个场景。"),
)


def scene_pack(body: dict) -> dict:
    req = urllib.request.Request(
        BASE + "/archive/scene_pack",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Archive-Token": TOKEN},
        method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    lines = ["# 后端换单排除对照 · 取样时间 %s" % time.strftime("%Y-%m-%dT%H:%M:%S%z"),
             "# 后端 = %s（seekdb embedded，令牌只从环境变量 ARCHIVE_TOKEN 读，本文件不含凭据）" % BASE,
             "# 被排除的「当前幕」= %s；两次调用之间只差 exclude_scene_id 这一个参数" % CURRENT,
             "# 请求体字段定义见 dify/archive/main.py 的 ScenePackBody，响应里的回显字段是 "
             "retrieval.structured_filters.reroll_excluded_scene_id"]
    ok = True
    for label, taste in TASTES:
        picked = {}
        for mode, extra in (("不排除", {}), ("排除当前幕", {"exclude_scene_id": CURRENT})):
            body = {"taste": taste, "scene_id": "", "revealed_csv": "", **extra}
            lines.append("")
            lines.append("%s | %s" % (label, mode))
            lines.append("   请求体   = %s" % json.dumps(body, ensure_ascii=False))
            try:
                out = scene_pack(body)
            except Exception as exc:
                lines.append("   !! 调用失败：%s" % exc)
                ok = False
                continue
            scene = out.get("scene") or {}
            retrieval = out.get("retrieval") or {}
            fused = ((retrieval.get("scene_choice") or {}).get("fused") or {}).get("hits") or []
            picked[mode] = scene.get("scene_id")
            lines.append("   fused 名次 = %s" % fused)
            lines.append("   选中场景 = %s（%s）｜可读规则 %d 条｜封存未解锁 %s"
                         % (scene.get("scene_id"), scene.get("title"),
                            len((out.get("pack") or {}).get("rule") or []),
                            [x.get("code") for x in (out.get("sealed_locked") or [])]))
            lines.append("   后端回显 reroll_excluded_scene_id = %r"
                         % ((retrieval.get("structured_filters") or {}).get("reroll_excluded_scene_id")))
        kept, fresh = picked.get("不排除"), picked.get("排除当前幕")
        if kept is None or fresh is None:
            continue
        if fresh == CURRENT:
            ok = False
            note = "!! 排除后还是同一幕"
        elif kept == CURRENT:
            note = "不排除时确实卡在上一幕，排除后才换得掉——这就是这条修复的证据"
        else:
            note = "这一句本身就不指向上一幕，排除与否都换得掉"
        lines.append("")
        lines.append("结论 · %s：不排除=%s → 排除后=%s；%s" % (label, kept, fresh, note))
    text = "\n".join(lines) + "\n"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print("wrote %s (ok=%s)" % (OUT, ok))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

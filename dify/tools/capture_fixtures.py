"""Capture real response bodies used by dify/tools/validate_dsl.py dry runs.

在 WSL 里跑：后端 127.0.0.1:8011、PowerContext 127.0.0.1:8000 都只在本机可见。
令牌从环境变量 ARCHIVE_TOKEN 读，默认值就是 .env.example 里的占位串，不写真实凭据。
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "capture"
OUT.mkdir(parents=True, exist_ok=True)

ARCH = "http://127.0.0.1:8011"
PC = "http://127.0.0.1:8000"
TOKEN = os.environ.get("ARCHIVE_TOKEN", "change-me-local-dev")
PLAYER = os.environ.get("ARCHIVE_FIXTURE_PLAYER", "A-VALIDATE-1")


def post(url, body, headers=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode("utf-8")


def dump(name, text):
    (OUT / name).write_text(text, encoding="utf-8")
    print(name, len(text))


def main():
    arch_h = {"X-Archive-Token": TOKEN}
    dump("scene_pack.json", post(ARCH + "/archive/scene_pack",
                                 {"taste": "我想要有声音、后背发凉但别太吓人的",
                                  "scene_id": "", "revealed_csv": ""}, arch_h))
    dump("search.json", post(ARCH + "/archive/search",
                             {"query": "我背对电梯门站着，想回头看一眼反光里多出来的影子",
                              "scene_id": "scn_office_elevator",
                              "kinds_csv": "rule,omen,item,npc,ending",
                              "keywords_csv": "反光板,回头,门",
                              "top_k": 6, "revealed_csv": ""}, arch_h))
    dump("scopes.json", post(PC + "/v1/scopes",
                             {"title": "午夜档案馆·工号%s" % PLAYER,
                              "summary": "规则怪谈玩家档案",
                              "idempotency_key": "archive-player-%s" % PLAYER}))
    scope_id = json.loads((OUT / "scopes.json").read_text(encoding="utf-8"))["scope_id"]
    # 与 DSL 里「裁决校验与记忆文案」的模板逐字一致，含 当前场景= 标签，
    # 这样离线 dry-run 用的就是线上真实写进 PowerContext 的文本形状。
    post(PC + "/v1/memory/remember", {"scope_id": scope_id, "kind": "violation",
                                      "text": "工号 %s 在《末班电梯只停偶数层》第 1 轮 当前场景=scn_office_elevator："
                                              "玩家打算按下 13 层；违反 RULE-ELV-02" % PLAYER,
                                      "reason": "dify-chatflow-turn"})
    post(PC + "/v1/memory/remember", {"scope_id": scope_id, "kind": "unlock",
                                      "text": "工号 %s 在《末班电梯只停偶数层》第 2 轮 当前场景=scn_office_elevator："
                                              "解锁封存规则 RULE-ELV-04" % PLAYER,
                                      "reason": "dify-chatflow-turn"})
    dump("prepare.json", post(PC + "/v1/context/prepare",
                              {"scope_id": scope_id,
                               "query": "%s 电梯,楼层,对讲机 违反 违规 解锁 封存 结局 预兆" % PLAYER,
                               "max_bytes": 6000}))
    dump("entries_list.json", post(PC + "/v1/memory/entries/list",
                                   {"scope_id": scope_id, "include_inactive": False}))


if __name__ == "__main__":
    main()

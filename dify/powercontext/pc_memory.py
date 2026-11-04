"""PowerContext 玩家记忆侧的运维脚本：建/复用 scope、回读记忆、导出验证证据。

Dify Chatflow 直接用 HTTP 节点调用 PowerContext 的 `/v1/scopes`、`/v1/context/prepare`、
`/v1/memory/remember`；本脚本只负责三件人在终端里才会做的事：

1. ``ensure`` —— 用与 Chatflow 完全相同的 idempotency_key 规则拿到玩家 scope，便于对照；
2. ``list`` / ``prepare`` —— 把记忆正文回读出来，作为「记忆被保存且在后续互动中被复用」的证据；
3. ``seed`` —— 预置一段历史（用于演示老玩家回归时 NPC 翻旧账）。

用法示例::

    python pc_memory.py ensure A-0713
    python pc_memory.py seed scp_xxx "工号 A-0713 在《末班电梯》违反 RULE-ELV-02，结局 END-ELV-01。"
    python pc_memory.py list scp_xxx
    python pc_memory.py prepare scp_xxx "A-0713 违规 结局"
    python pc_memory.py evidence scp_xxx out.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

BASE_URL = os.environ.get("POWERCONTEXT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
# 与 DSL 中保持一致的幂等键前缀：同一工号永远落到同一个 scope，跨会话也就不需要重新建档
KEY_PREFIX = os.environ.get("ARCHIVE_SCOPE_KEY_PREFIX", "archive-player-")


def normalise_player_id(player_id: str) -> str:
    """必须与 Chatflow「组装请求体」节点里的 _pid() 完全一致，否则同一工号会建出两个 scope。"""
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._\-]", "", str(player_id or ""))[:32]
    return cleaned or "ANON"


def _post(path: str, body: Dict[str, Any], timeout: int = 40) -> Dict[str, Any]:
    req = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {exc.code} on {path}: {detail[:300]}") from exc


def ensure_scope(player_id: str) -> str:
    """与 Chatflow 的「建档」HTTP 节点完全一致的参数。

    PowerContext 要求同一 idempotency_key 必须携带**逐字相同**的参数，否则返回
    ``scope_idempotency_conflict``，因此 title/summary 只能由工号确定性地拼出。
    """
    pid = normalise_player_id(player_id)
    out = _post(
        "/v1/scopes",
        {
            "title": f"午夜档案馆·工号{pid}",
            "summary": "规则怪谈玩家档案",
            "idempotency_key": f"{KEY_PREFIX}{pid}",
        },
    )
    return out["scope_id"]


def remember(scope_id: str, text: str, kind: str = "episode") -> Dict[str, Any]:
    return _post("/v1/memory/remember", {"scope_id": scope_id, "kind": kind, "text": text, "reason": "manual-seed"})


def list_entries(scope_id: str) -> Dict[str, Any]:
    return _post("/v1/memory/entries/list", {"scope_id": scope_id, "include_inactive": False})


def prepare(scope_id: str, query: str, max_bytes: int = 6000) -> Dict[str, Any]:
    return _post("/v1/context/prepare", {"scope_id": scope_id, "query": query, "max_bytes": max_bytes})


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(description="PowerContext 玩家记忆运维/取证")
    p.add_argument("cmd", choices=["ensure", "seed", "list", "prepare", "evidence"])
    p.add_argument("arg1", help="ensure 用 player_id（工号）；其余用 scope_id")
    p.add_argument("arg2", nargs="?", default="", help="seed 的记忆正文 / prepare 的检索词 / evidence 的输出文件")
    a = p.parse_args(argv)
    print(f"[pc] base={BASE_URL}", file=sys.stderr)

    if a.cmd == "ensure":
        sid = ensure_scope(a.arg1)
        print(json.dumps({"player_id": a.arg1, "scope_id": sid}, ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "seed":
        out = remember(a.arg1, a.arg2)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "list":
        out = list_entries(a.arg1)
        print(json.dumps(out, ensure_ascii=False, indent=2)[:4000])
        return 0
    if a.cmd == "prepare":
        out = prepare(a.arg1, a.arg2 or "违规 结局 偏好")
        print(out.get("content", "")[:4000])
        return 0
    # evidence：一次性把记忆条目与准备上下文写进同一个 JSON，供提交材料引用
    sid = a.arg1
    bundle = {
        "scope_id": sid,
        "base_url": BASE_URL,
        "query": "违规 结局 偏好",
        "entries": list_entries(sid),
        "prepared": prepare(sid, "违规 结局 偏好"),
    }
    if a.arg2:
        with open(a.arg2, "w", encoding="utf-8") as fh:
            json.dump(bundle, fh, ensure_ascii=False, indent=2)
        print(f"[pc] evidence -> {a.arg2}", file=sys.stderr)
    print(json.dumps(bundle, ensure_ascii=False, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

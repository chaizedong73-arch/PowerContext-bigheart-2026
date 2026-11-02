"""模型产出应用：解析 JSON 输出、校验路径、写入文件、生成 unified diff 记录。"""

import difflib
import json
import re
from pathlib import Path

from . import CtxsddError

ALLOWED_PREFIX = "business/"


class PatchError(CtxsddError):
    pass


def parse_model_output(text: str) -> dict:
    """从模型输出提取 JSON：{"summary": "...", "files": [{"path", "content"}]}。"""
    candidates = []
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        candidates.append(fenced.group(1))
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : i + 1])
                    break
    for cand in candidates:
        try:
            parsed = json.loads(cand)
        except Exception:
            continue
        if isinstance(parsed, dict) and "files" in parsed:
            return parsed
    raise PatchError("无法从模型输出解析出合法的 JSON 文件清单")


def validate_files(parsed: dict) -> list[dict]:
    files = parsed.get("files")
    if not isinstance(files, list) or not files:
        raise PatchError("模型输出 files 为空")
    out = []
    for f in files:
        if not isinstance(f, dict) or "path" not in f or "content" not in f:
            raise PatchError(f"文件条目格式非法: {f!r}")
        p = str(f["path"]).replace("\\", "/").lstrip("/")
        if p.startswith("..") or "/../" in p:
            raise PatchError(f"路径越界: {p}")
        if not p.startswith(ALLOWED_PREFIX):
            raise PatchError(f"只允许修改 {ALLOWED_PREFIX} 下的文件，收到: {p}")
        out.append({"path": p, "content": str(f["content"])})
    return out


def apply_files(root: Path, files: list[dict]) -> list[str]:
    written = []
    for f in files:
        target = root / f["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f["content"], encoding="utf-8")
        written.append(f["path"])
    return written


def snapshot_tree(business_dir: Path, root: Path) -> dict[str, str]:
    """抓取 business/ 下全部 .py 文件内容，键为相对 root 的路径。"""
    snap = {}
    for p in sorted(business_dir.rglob("*.py")):
        snap[p.relative_to(root).as_posix()] = p.read_text(encoding="utf-8")
    return snap


def unified_diff(before: dict[str, str], after: dict[str, str]) -> str:
    lines = []
    for p in sorted(set(before) | set(after)):
        b = before.get(p, "").splitlines()
        a = after.get(p, "").splitlines()
        if b == a:
            continue
        lines.extend(
            difflib.unified_diff(
                b, a, fromfile=f"a/{p}", tofile=f"b/{p}", lineterm=""
            )
        )
        lines.append("")
    return "\n".join(lines)

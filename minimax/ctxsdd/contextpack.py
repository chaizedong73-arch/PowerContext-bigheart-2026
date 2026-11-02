"""上下文打包：把 seekdb 检索到的规格、代码、测试组装成带来源标注的 ContextPack。"""

import hashlib
import json
import time
from pathlib import Path

SYSTEM_PROMPT = (
    "你是一名严谨的软件工程助手，正在一个规格驱动开发（SDD）流水线中工作。"
    "你会收到：当前有效的规格（含来源）、相关代码、相关测试与测试失败信息。"
    "你必须只输出一个 JSON 对象，格式为：\n"
    '{"summary": "一句话说明你做了什么", '
    '"files": [{"path": "business/.../file.py", "content": "<完整的新文件内容>"}]}\n'
    "要求：\n"
    "1. 只允许修改 business/ 目录下的文件；content 必须是文件的完整最终内容，不要省略。\n"
    "2. 严格按规格实现；保持既有函数行为不变，保证回归测试通过。\n"
    "3. 除 JSON 外不要输出任何其他文字、注释或代码围栏说明。"
)


def _pack_id(task_desc: str) -> str:
    digest = hashlib.md5((task_desc + time.strftime("%Y%m%d%H%M%S")).encode()).hexdigest()[:10]
    return f"pack_{digest}"


def build_pack(
    task_desc: str,
    spec_hits: list[dict],
    code_hits: list[dict],
    test_hits: list[dict],
    direct_files: list[str],
    root: Path,
) -> dict:
    """spec_hits 已按最新版本过滤；direct_files 是任务指定必须纳入的目标文件。"""
    pack = {
        "pack_id": _pack_id(task_desc),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "task": task_desc,
        "spec_sources": [],
        "code_sources": [],
        "test_sources": [],
        "notes": [],
    }
    for h in spec_hits:
        m = h["metadata"]
        pack["spec_sources"].append(
            {
                "origin": "seekdb:ctxsdd_specs",
                "chunk_id": h["id"],
                "source": m.get("source"),
                "version": m.get("version"),
                "heading": m.get("heading"),
                "distance": h.get("distance"),
                "text": h["document"],
            }
        )
    seen = set()
    for section, hits, origin_key in (
        ("code_sources", code_hits, "code"),
        ("test_sources", test_hits, "test"),
    ):
        seen_section = set()
        for h in hits:
            rel = h["metadata"].get("path")
            if not rel or rel in seen_section or rel in seen:
                continue
            seen_section.add(rel)
            seen.add(rel)
            path = root / rel
            content = path.read_text(encoding="utf-8") if path.exists() else h["document"]
            pack[section].append(
                {"origin": "seekdb", "collection": f"ctxsdd_{origin_key}", "path": rel, "content": content}
            )
        pack["notes"].append(
            f"{section} 来自 seekdb 集合 ctxsdd_{origin_key} 的实际查询结果"
        )
    for rel in direct_files:
        if rel in seen:
            continue
        path = root / rel
        if not path.exists():
            continue
        section = "test_sources" if "/tests/" in rel else "code_sources"
        pack[section].append(
            {"origin": "task_target", "path": rel, "content": path.read_text(encoding="utf-8")}
        )
        pack["notes"].append(f"{rel} 为任务指定目标文件，直接纳入打包")
    return pack


def write_json(pack: dict, path: Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")


def render_user_prompt(pack: dict) -> str:
    parts = [f"# 开发任务\n\n{pack['task']}", "# 当前有效规格（seekdb 检索）\n"]
    for s in pack["spec_sources"]:
        parts.append(
            f"## 规格 {s['source']} v{s['version']} · {s['heading']}"
            f"\n（来源: {s['origin']} #{s['chunk_id']}）\n\n{s['text']}\n"
        )
    parts.append("# 相关代码\n")
    for c in pack["code_sources"]:
        parts.append(f"## {c['path']}（来源: {c['origin']}）\n\n```python\n{c['content']}\n```\n")
    parts.append("# 相关测试\n")
    for t in pack["test_sources"]:
        parts.append(f"## {t['path']}（来源: {t['origin']}）\n\n```python\n{t['content']}\n```\n")
    parts.append(
        "# 输出要求\n\n"
        "只输出一个 JSON 对象（可放在 ```json 围栏中）：\n"
        '{"summary": "...", "files": [{"path": "business/...", "content": "<完整新文件内容>"}]}\n'
        "修改必须让上述失败的目标测试通过，同时保持回归测试全部通过。"
    )
    return "\n".join(parts)

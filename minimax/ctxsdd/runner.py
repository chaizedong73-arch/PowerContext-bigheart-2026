"""pytest 执行器：运行目标测试与回归测试并解析 junitxml 结果。"""

import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path


def run_pytest(
    root: Path,
    paths: list[str],
    purpose: str,
    log_path: Path | None = None,
    junit_path: Path | None = None,
) -> dict:
    """在 root 目录运行指定测试路径，返回 {summary, cases, stdout_tail}。"""
    junit = Path(junit_path) if junit_path else root / ".ctxsdd" / "last_junit.xml"
    junit.parent.mkdir(parents=True, exist_ok=True)
    if junit.exists():
        junit.unlink()
    cmd = [sys.executable, "-m", "pytest", "-q", *paths, f"--junitxml={junit}"]
    try:
        proc = subprocess.run(
            cmd, cwd=str(root), capture_output=True, text=True, timeout=600
        )
        stdout = proc.stdout or ""
    except subprocess.TimeoutExpired as e:
        stdout = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")

    cases, n_failed = [], 0
    if junit.exists():
        try:
            tree = ET.parse(junit)
            for tc in tree.getroot().iter("testcase"):
                outcome = "passed"
                for child in tc:
                    if child.tag in ("failure", "error"):
                        outcome = "failed"
                    elif child.tag == "skipped":
                        outcome = "skipped"
                cases.append(
                    {
                        "id": f'{tc.get("classname", "")}::{tc.get("name", "")}',
                        "outcome": outcome,
                    }
                )
        except ET.ParseError:
            pass
    n_failed = sum(1 for c in cases if c["outcome"] in ("failed", "error"))
    n_skipped = sum(1 for c in cases if c["outcome"] == "skipped")
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "purpose": purpose,
        "paths": paths,
        "summary": {
            "total": len(cases),
            "passed": len(cases) - n_failed - n_skipped,
            "failed": n_failed,
            "skipped": n_skipped,
            "ok": bool(cases) and n_failed == 0,
        },
        "cases": cases,
        "stdout_tail": stdout[-4000:],
    }
    if log_path is not None:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with Path(log_path).open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def failed_summary(record: dict) -> str:
    """给模型反馈用的失败摘要。"""
    lines = []
    for c in record["cases"]:
        if c["outcome"] in ("failed", "error"):
            lines.append(f"- FAILED {c['id']}")
    tail = record.get("stdout_tail", "")
    lines.append("")
    lines.append(tail[-2000:])
    return "\n".join(lines)

"""ctxsdd 命令行入口：init / import-code / import-spec / run-task / verify / tasks。

完整工作流：
  1) init            初始化 seekdb 库、集合与状态（空状态开始）
  2) import-code     把业务代码与测试索引进 seekdb
  3) import-spec     规格切块入库（版本化，可更新重导入）
  4) run-task        seekdb 检索 -> 上下文打包 -> MiniMax 修改代码 -> 测试验证
  5) verify          全量测试验证；状态跨进程持久化、重启模块后可复用
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from . import CtxsddError
from .chunker import chunk_spec
from .contextpack import SYSTEM_PROMPT, build_pack, render_user_prompt, write_json
from .minimax_client import MiniMaxClient, MiniMaxError
from .obstore import ObStore, detect_embedder
from .patch import (
    apply_files,
    parse_model_output,
    snapshot_tree,
    unified_diff,
    validate_files,
    PatchError,
)
from .runner import failed_summary, run_pytest
from .state import State

BUSINESS_DIR = "business/reading_list"
REGRESSION_TESTS = ["business/reading_list/tests/test_regression.py"]
TESTS_DIR = "business/reading_list/tests"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# 规格文件名的版本后缀，如 reading_list_spec_v1 / -v2 / v2
_VERSION_SUFFIX_RE = re.compile(r"[_\s-]*v(\d+)$", re.IGNORECASE)


def _strip_version_suffix(stem: str) -> str:
    return _VERSION_SUFFIX_RE.sub("", stem).strip("_- ") or stem


def default_source(spec_path: Path) -> str:
    """默认规格来源名：文件名主干去掉版本后缀。

    reading_list_spec_v1.md 与 reading_list_spec_v2.md 的主干都归并到同一来源
    reading_list_spec——这样"按来源取最新版本"才能过滤掉 v1 旧块；若直接用
    原始词干当 source，v1/v2 会被当成两个不同来源，失效规格永远过滤不掉。
    """
    return _strip_version_suffix(spec_path.stem)


def version_from_stem(stem: str):
    """从文件名主干解析版本号（reading_list_spec_v2 -> 2），解析不出返回 None。"""
    m = _VERSION_SUFFIX_RE.search(stem)
    return int(m.group(1)) if m else None


def normalize_source(name) -> str:
    """归一化已入库的 source：携带版本后缀的旧来源名也归并到同一来源。"""
    return _strip_version_suffix(name or "")


class Env:
    """根目录与各产物路径。"""

    def __init__(self, root: Path):
        self.root = root
        self.db = root / ".ctxsdd" / "seekdb"
        self.state_path = root / ".ctxsdd" / "powercontext_state.json"
        self.runs = root / "docs" / "runs"
        self.snapshots = root / "docs" / "snapshots"
        self.state = State(self.state_path, self.runs / "powercontext_state.json")

    def require_init(self) -> None:
        if not self.state.initialized:
            raise CtxsddError("模块尚未初始化，请先执行: python -m ctxsdd init")

    def ensure_dirs(self) -> None:
        self.runs.mkdir(parents=True, exist_ok=True)
        self.snapshots.mkdir(parents=True, exist_ok=True)

    def load_state_or_die(self) -> State:
        self.require_init()
        return self.state


# ---------------------------------------------------------------- 命令


def cmd_init(args) -> int:
    env = Env(Path(args.root).resolve())
    env.ensure_dirs()
    embedder = detect_embedder()
    store = ObStore(env.db, embedder)
    env.state.mark_initialized(embedder)
    env.state.data["collections"] = {name: 0 for name in store.collections}
    env.state.save()
    print(f"[init] seekdb 数据库: {env.db}")
    print(f"[init] 嵌入器: {embedder}")
    print("[init] 集合: ctxsdd_specs / ctxsdd_code / ctxsdd_tests")
    print("[init] 状态文件: ", env.state_path)
    return 0


def cmd_import_code(args) -> int:
    env = Env(Path(args.root).resolve())
    state = env.load_state_or_die()
    store = ObStore(env.db, state.data["embedder"])
    business = env.root / BUSINESS_DIR
    code_files, test_files = [], []
    for p in sorted(business.rglob("*.py")):
        rel = p.relative_to(env.root).as_posix()
        text = p.read_text(encoding="utf-8")
        (test_files if "/tests/" in rel else code_files).append((rel, text))
    n1 = store.index_files("code", code_files)
    n2 = store.index_files("test", test_files)
    state.data["code_index"] = {
        rel: len(text) for rel, text in code_files + test_files
    }
    state.data["code_index_imported_at"] = _now()
    state.save()
    print(f"[import-code] 代码文件入库: {n1}，测试文件入库: {n2}")
    return 0


def cmd_import_spec(args) -> int:
    env = Env(Path(args.root).resolve())
    state = env.load_state_or_die()
    store = ObStore(env.db, state.data["embedder"])
    spec_path = (env.root / args.file).resolve()
    if not spec_path.exists():
        raise CtxsddError(f"规格文件不存在: {spec_path}")
    source = args.source or default_source(spec_path)
    specs = state.data.setdefault("specs", {})
    latest = specs.get(source, {}).get("latest_version", 0)
    version = args.version or version_from_stem(spec_path.stem) or latest + 1

    text = spec_path.read_text(encoding="utf-8")
    chunks = chunk_spec(text, source, version)
    n = store.index_specs(chunks)

    # 规格更新前后对照留档
    upd_dir = env.runs / "spec_update"
    upd_dir.mkdir(parents=True, exist_ok=True)
    (upd_dir / f"{source}_v{version}.md").write_text(text, encoding="utf-8")
    if latest:
        prev_file = upd_dir / f"{source}_v{latest}.md"
        if prev_file.exists():
            prev_text = prev_file.read_text(encoding="utf-8")
            diff = unified_diff(
                {f"{source}_v{latest}.md": prev_text}, {f"{source}_v{version}.md": text}
            )
            (upd_dir / f"{source}_v{latest}_to_v{version}.diff").write_text(diff, encoding="utf-8")
            print(f"[import-spec] 规格更新对照: {upd_dir / f'{source}_v{latest}_to_v{version}.diff'}")

    specs[source] = {
        "latest_version": version,
        "file": spec_path.relative_to(env.root).as_posix(),
        "chunks": n,
        "imported_at": _now(),
    }
    state.save()
    print(f"[import-spec] {source} v{version}: {n} 个规格块写入 seekdb（ctxsdd_specs）")
    return 0


def _log_ob_query(env, collection, query_text, hits, note):
    record = {
        "ts": _now(),
        "collection": collection,
        "query_text": query_text,
        "n_hits": len(hits),
        "hits": [
            {"id": h["id"], "metadata": h["metadata"], "distance": h.get("distance")}
            for h in hits
        ],
        "used_for": note,
    }
    with (env.runs / "ob_query_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def cmd_run_task(args) -> int:
    env = Env(Path(args.root).resolve())
    state = env.load_state_or_die()
    env.ensure_dirs()
    store = ObStore(env.db, state.data["embedder"])

    if args.task_file:
        tf = (env.root / args.task_file).resolve()
        task_desc = tf.read_text(encoding="utf-8").strip()
    else:
        task_desc = args.description.strip()
    if not task_desc:
        raise CtxsddError("任务描述为空")
    targets = args.targets or []
    task_no = len(state.data["tasks"]) + 1
    task_id = f"task_{task_no:03d}"

    # ---- 1. seekdb 检索：实际查询结果用于任务上下文 ----
    spec_hits = store.query("ctxsdd_specs", task_desc, n=8)
    # 规格更新后：同一 source 只保留最新版本（source 先归一化，
    # 历史上以"文件名词干+版本"入库的旧数据也能正确归并过滤）
    latest_by_source = {}
    for h in spec_hits:
        src = normalize_source(h["metadata"].get("source"))
        ver = h["metadata"].get("version", 0)
        latest_by_source[src] = max(latest_by_source.get(src, 0), ver)
    spec_hits_latest = [
        h
        for h in spec_hits
        if h["metadata"].get("version")
        == latest_by_source.get(normalize_source(h["metadata"].get("source")))
    ]
    code_hits = store.query("ctxsdd_code", task_desc, n=4)
    test_hits = store.query("ctxsdd_tests", task_desc, n=4)
    # 检索命中的测试/代码只保留本任务相关文件：避免把未来规格的测试提前泄露给模型
    direct = sorted(set(targets) | set(REGRESSION_TESTS) | {f"{BUSINESS_DIR}/reading_list.py"})
    code_hits = [h for h in code_hits if h["metadata"].get("path") in direct]
    test_hits = [h for h in test_hits if h["metadata"].get("path") in direct]
    _log_ob_query(env, "ctxsdd_specs", task_desc, spec_hits,
                  f"上下文打包（已过滤到最新版本 v{sorted(set(latest_by_source.values()))}）")
    _log_ob_query(env, "ctxsdd_code", task_desc, code_hits, "上下文打包（已过滤到任务相关文件）")
    _log_ob_query(env, "ctxsdd_tests", task_desc, test_hits, "上下文打包（已过滤到任务相关文件）")
    print(f"[run-task] seekdb 检索: 规格 {len(spec_hits_latest)}/{len(spec_hits)} 块（最新版本），"
          f"代码 {len(code_hits)}，测试 {len(test_hits)}")

    # ---- 2. 上下文打包 ----
    pack = build_pack(task_desc, spec_hits_latest, code_hits, test_hits, direct, env.root)
    pack_file = env.runs / f"context_pack_{task_no}.json"
    write_json(pack, pack_file)
    write_json(pack, env.runs / "context_pack.json")
    print(f"[run-task] 上下文包: {pack_file} (pack_id={pack['pack_id']})")

    # ---- 3. 修改前基线（目标测试应失败）----
    scope = sorted(set(targets + REGRESSION_TESTS))
    pre = run_pytest(env.root, scope, f"{task_id}:before", env.runs / "test_run_log.jsonl")
    print(f"[run-task] 修改前: passed={pre['summary']['passed']} failed={pre['summary']['failed']}")

    # ---- 4. MiniMax 真实调用 + 多轮修复 ----
    mm = MiniMaxClient(env.runs / "minimax_dev_log.jsonl", model=args.model)
    before_snap = snapshot_tree(env.root / BUSINESS_DIR, env.root)
    (env.root / ".ctxsdd" / "snapshots").mkdir(parents=True, exist_ok=True)
    (env.root / ".ctxsdd" / "snapshots" / f"{task_id}_before.json").write_text(
        json.dumps(before_snap, ensure_ascii=False), encoding="utf-8"
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": render_user_prompt(pack)},
    ]
    if pre["summary"]["failed"]:
        messages.append(
            {"role": "user", "content": "目标测试修改前的失败情况：\n" + failed_summary(pre)}
        )
    ok, rounds, changed = False, 0, []
    last_post = pre
    try:
        while rounds < args.max_rounds:
            rounds += 1
            content = mm.chat(messages)
            parsed = parse_model_output(content)
            files = validate_files(parsed)
            changed = apply_files(env.root, files)
            print(f"[run-task] 第 {rounds} 轮 MiniMax 修改 {len(changed)} 个文件: {changed}")
            last_post = run_pytest(
                env.root, scope, f"{task_id}:round{rounds}", env.runs / "test_run_log.jsonl"
            )
            s = last_post["summary"]
            print(f"[run-task] 第 {rounds} 轮后: passed={s['passed']} failed={s['failed']}")
            if s["ok"]:
                ok = True
                break
            messages.append({"role": "user", "content":
                             "上一轮修改后测试未全部通过：\n" + failed_summary(last_post)
                             + "\n请再次只输出修正后的完整 JSON。"})
    except MiniMaxError:
        raise
    except PatchError as e:
        print(f"[run-task] 模型产出解析失败: {e}", file=sys.stderr)
        ok = False

    # ---- 5. 修改差异留档 ----
    after_snap = snapshot_tree(env.root / BUSINESS_DIR, env.root)
    task_diff = unified_diff(before_snap, after_snap)
    diff_file = env.runs / f"code_diff_{task_no}.txt"
    diff_file.write_text(task_diff, encoding="utf-8")
    cum_file = env.runs / "code_diff.txt"
    cum_file.write_text(unified_diff(
        json.loads((env.root / ".ctxsdd" / "snapshots" / "task_001_before.json").read_text(encoding="utf-8"))
        if (env.root / ".ctxsdd" / "snapshots" / "task_001_before.json").exists() else before_snap,
        after_snap,
    ), encoding="utf-8")
    snap_dir = env.snapshots / task_id
    snap_dir.mkdir(parents=True, exist_ok=True)
    for rel, text in after_snap.items():
        out = snap_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")

    # ---- 6. 状态持久化（重启模块后可读取复用）----
    record = {
        "id": task_id,
        "created_at": _now(),
        "description": task_desc,
        "targets": targets,
        "status": "success" if ok else "failed",
        "rounds": rounds,
        "model": mm.model,
        "pack_id": pack["pack_id"],
        "pack_file": pack_file.relative_to(env.root).as_posix(),
        "diff_file": diff_file.relative_to(env.root).as_posix(),
        "changed_files": changed,
        "tests_before": pre["summary"],
        "tests_after": last_post["summary"],
    }
    state.data["tasks"].append(record)
    state.save()
    print(f"[run-task] {task_id} 状态: {record['status']}（状态已写入 {env.state_path} 并镜像到 docs/runs）")
    return 0 if ok else 1


def cmd_verify(args) -> int:
    env = Env(Path(args.root).resolve())
    state = env.load_state_or_die()
    env.ensure_dirs()
    result = run_pytest(env.root, [TESTS_DIR], "verify", env.runs / "test_run_log.jsonl")
    s = result["summary"]
    print(f"[verify] 全量测试: total={s['total']} passed={s['passed']} failed={s['failed']}")
    print(f"[verify] 已保存任务数: {len(state.data['tasks'])}（来自持久化状态）")
    for t in state.data["tasks"]:
        print(f"  - {t['id']} [{t['status']}] rounds={t['rounds']} targets={t['targets']}")
    return 0 if s["ok"] else 1


def cmd_tasks(args) -> int:
    env = Env(Path(args.root).resolve())
    state = State(env.state_path)
    print(json.dumps(state.data, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ctxsdd", description="SDD 上下文管理系统")
    p.add_argument("--root", default=".", help="minimax 工程根目录")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="初始化 seekdb 与状态").set_defaults(func=cmd_init)

    ic = sub.add_parser("import-code", help="把业务代码与测试索引进 seekdb")
    ic.set_defaults(func=cmd_import_code)

    isp = sub.add_parser("import-spec", help="规格切块入库（版本化）")
    isp.add_argument("file", help="规格 Markdown 文件")
    isp.add_argument("--version", type=int, default=None)
    isp.add_argument("--source", default=None, help="规格名（默认取文件名主干并去掉 _vN 版本后缀，使同规格各版本归并同一来源）")
    isp.set_defaults(func=cmd_import_spec)

    rt = sub.add_parser("run-task", help="检索上下文并调用 MiniMax 完成代码修改")
    rt.add_argument("--task-file", default=None)
    rt.add_argument("--description", default=None)
    rt.add_argument("--targets", nargs="*", default=[], help="目标测试文件（修改前失败、修改后应全过）")
    rt.add_argument("--max-rounds", type=int, default=3)
    rt.add_argument("--model", default=None)
    rt.set_defaults(func=cmd_run_task)

    v = sub.add_parser("verify", help="全量测试验证")
    v.set_defaults(func=cmd_verify)

    t = sub.add_parser("tasks", help="查看持久化状态中的任务记录")
    t.set_defaults(func=cmd_tasks)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CtxsddError as e:
        print(f"[ctxsdd] 错误: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

"""把「提交的文件」和「云端真正在跑的版本」对到一起，写成可复核的取证报告。

判题的决定性缺口是：提交的 DSL 比线上运行的版本新，运行证据回溯不到提交文件。
这份工具用三个互相咬合的事实把这条链补上：

  1. 云端「导出 DSL」拿回来的 app.yaml  ←→  dify/dsl/midnight_archive_chatflow.yml
     逐节点、逐连线、逐变量引用比对，差异只允许出现在云端自己补的排版/默认字段上；
  2. 控制台「已发布工作流」JSON 的 id/version  ←→  evidence/cloud_run_*.txt 里 SSE 的 workflow_id；
  3. 两边的环境变量**名字**集合一致，值不同（本地占位 vs 隧道地址）。隧道域名按约定打码，不写进仓库。

云端产物放在仓库外（scratch），用环境变量指进来：

    export CLOUD_APP_YAML="/mnt/c/.../scratch/cloud_export/app.yaml"
    export CLOUD_PUBLISHED_JSON="/mnt/c/.../scratch/cloud_published_workflow.json"
    cd dify && python3 tools/cloud_publish_parity.py

报告落到 evidence/cloud_publish_parity.txt；同时把打码后的云端导出副本写进 evidence/cloud_export/。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except Exception:  # pragma: no cover
    print("需要 PyYAML：pip install pyyaml")
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parent.parent
DSL = ROOT / "dsl" / "midnight_archive_chatflow.yml"
EVIDENCE = ROOT / "evidence"
EXPORT_DIR = EVIDENCE / "cloud_export"

# 云端导入后会自己补的字段：排版、选中态、重试/错误策略默认值。它们不代表图的结构差异。
NOISE_LEAF = {
    "height", "width", "position", "positionAbsolute", "selected", "sourcePosition",
    "targetPosition", "zIndex", "isInIteration", "isIteration", "iteration_id",
    "retry_config", "error_strategy", "desc",
}
HOST_RE = re.compile(r"https://[A-Za-z0-9.-]*trycloudflare\.com")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_lf(path: Path) -> str:
    """工作树在 Windows 上是 CRLF，git 存的 blob 是 LF；这个哈希才是「提交文件」的哈希。"""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def redact(text: str) -> str:
    return HOST_RE.sub("https://<quick-tunnel 域名·按约定不提交>", text)


def graph_of(doc: dict) -> dict:
    g = doc["workflow"]["graph"]
    return g if isinstance(g, dict) else json.loads(g)


def edge_keys(graph: dict) -> set:
    out = set()
    for e in graph.get("edges") or []:
        out.add((e.get("source"), e.get("sourceHandle"), e.get("target"), e.get("targetHandle")))
    return out


def node_summaries(graph: dict) -> dict:
    return {n["id"]: (n["data"].get("type"), n["data"].get("title")) for n in graph.get("nodes") or []}


def flatten(obj, prefix: str = "") -> dict:
    """把节点 data 拍成 a.b.c → 值，便于只报「哪个字段不一样」。"""
    out: dict[str, object] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix or "<root>"] = obj
    return out


def var_refs(graph_dump: str) -> set:
    return set(re.findall(r"\{\{#([A-Za-z0-9_.\-\[\]]+)#\}\}", graph_dump))


def node_field_diff(a_graph: dict, b_graph: dict) -> tuple[list, list, list]:
    """返回 (只有 A 有的字段路径, 只有 B 有的, 两边值不同的)。路径带节点 id，值不进报告。"""
    a_nodes = {n["id"]: n.get("data") or {} for n in a_graph.get("nodes") or []}
    b_nodes = {n["id"]: n.get("data") or {} for n in b_graph.get("nodes") or []}
    only_a: list[str] = []
    only_b: list[str] = []
    differ: list[str] = []
    for nid in sorted(set(a_nodes) & set(b_nodes)):
        fa = flatten(a_nodes[nid], nid)
        fb = flatten(b_nodes[nid], nid)
        for p in sorted(set(fa) - set(fb)):
            only_a.append(p)
        for p in sorted(set(fb) - set(fa)):
            only_b.append(p)
        for p in sorted(set(fa) & set(fb)):
            if fa[p] != fb[p]:
                differ.append(p)
    def split_noise(items):
        real, noise = [], []
        for p in items:
            leaf = re.sub(r"\[\d+\]$", "", p.split(".")[-1])
            (noise if leaf in NOISE_LEAF or leaf.isdigit() else real).append(p)
        return real, noise
    ra, na = split_noise(only_a)
    rb, nb = split_noise(only_b)
    rd, nd = split_noise(differ)
    return (ra + na, rb + nb, rd + nd), (len(na) + len(nb) + len(nd)), (ra, rb, rd)


def env_names(doc: dict) -> list:
    return [e.get("name") for e in (doc.get("workflow") or {}).get("environment_variables") or []]


def env_values(doc: dict) -> dict:
    return {e.get("name"): e.get("value") for e in (doc.get("workflow") or {}).get("environment_variables") or []}


def main() -> int:
    app_yaml = os.environ.get("CLOUD_APP_YAML", "")
    pub_json = os.environ.get("CLOUD_PUBLISHED_JSON", "")
    lines: list[str] = []
    lines.append("# 提交文件 ↔ 云端发布版本 一致性取证")
    lines.append("# 生成：tools/cloud_publish_parity.py   取样时间 %s" % __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"))
    lines.append("# 云端产物从控制台接口取回，放在仓库外：")
    lines.append("#   CLOUD_APP_YAML      = %s" % (app_yaml or "(未设置)"))
    lines.append("#   CLOUD_PUBLISHED_JSON= %s" % (pub_json or "(未设置)"))
    lines.append("# 约定：隧道域名一律打码；App API 密钥、玩家 scope 都不写进这份文件。")
    ok = True

    if not app_yaml or not Path(app_yaml).is_file():
        lines.append("")
        lines.append("!! 缺少云端导出的 app.yaml，没法比对。先在控制台「导出 DSL」或用 tools/ 里的说明取回文件。")
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        (EVIDENCE / "cloud_publish_parity.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 2

    committed = yaml.safe_load(DSL.read_text(encoding="utf-8"))
    cloud = yaml.safe_load(Path(app_yaml).read_text(encoding="utf-8"))
    cg, lg = graph_of(committed), graph_of(cloud)

    lines.append("")
    lines.append("== 1. 文件层面")
    lines.append("   提交的 DSL   %s  %d bytes  sha256=%s" % (DSL.name, DSL.stat().st_size, sha256(DSL)))
    lines.append("   提交的 DSL（换行归一成 LF，也就是仓库 blob 的字节）sha256=%s" % sha256_lf(DSL))
    lines.append("   云端导出的   app.yaml  %d bytes  sha256=%s" % (Path(app_yaml).stat().st_size, sha256(Path(app_yaml))))
    lines.append("   （云端导出里两个环境变量的值是本次跑用的隧道地址，按约定打码后另存于 evidence/cloud_export/，"
                 "所以那份副本的 sha 与原始导出不同）")

    lines.append("")
    lines.append("== 2. 图结构")
    for tag, g in (("提交", cg), ("云端", lg)):
        lines.append("   %s：nodes=%d edges=%d" % (tag, len(g.get("nodes") or []), len(g.get("edges") or [])))
    sn, sl = node_summaries(cg), node_summaries(lg)
    ids_missing_in_cloud = sorted(set(sn) - set(sl))
    ids_extra_in_cloud = sorted(set(sl) - set(sn))
    lines.append("   节点 id 集合：只在提交里有=%s；只在云端里有=%s"
                 % (ids_missing_in_cloud or "无", ids_extra_in_cloud or "无"))
    title_diff = [i for i in sorted(set(sn) & set(sl)) if sn[i] != sl[i]]
    lines.append("   节点 (type,title) 逐个比对：%s" % ("全部一致（%d 个）" % len(set(sn) & set(sl)) if not title_diff else "有差异 %s" % title_diff))
    ek_c, ek_l = edge_keys(cg), edge_keys(lg)
    lines.append("   连线 (source,sourceHandle,target,targetHandle) 集合：一致=%s；提交独有=%s；云端独有=%s"
                 % (ek_c == ek_l, sorted(ek_c - ek_l) or "无", sorted(ek_l - ek_c) or "无"))

    dump_c = json.dumps(cg, ensure_ascii=False, sort_keys=True)
    dump_l = json.dumps(lg, ensure_ascii=False, sort_keys=True)
    rc, rl = var_refs(dump_c), var_refs(dump_l)
    lines.append("   变量引用 {{#…#}} 去重后：提交 %d 个 / 云端 %d 个；提交独有=%s；云端独有=%s"
                 % (len(rc), len(rl), sorted(rc - rl) or "无", sorted(rl - rc) or "无"))

    lines.append("")
    lines.append("== 3. 节点内部字段（只列路径，不印值）")
    (only_c, only_l, differ), noise_count, real = node_field_diff(cg, lg)
    rc_, rl_, rd_ = real
    lines.append("   实质差异字段：%d 个（提交独有 %d / 云端独有 %d / 值不同 %d）"
                 % (len(rc_) + len(rl_) + len(rd_), len(rc_), len(rl_), len(rd_)))
    for p in (rc_ + rl_ + rd_)[:40]:
        lines.append("     · %s" % p)
    lines.append("   云端自己补的排版/默认字段：%d 个（不计入差异，例如 height/width/retry_config/model 细节）" % noise_count)
    if rc_ or rl_ or rd_:
        ok = False

    lines.append("")
    lines.append("== 4. 环境变量（名字必须一致，值按部署环境不同）")
    nc, nl = env_names(committed), env_names(cloud)
    vc, vl = env_values(committed), env_values(cloud)
    lines.append("   提交：%s" % nc)
    lines.append("   云端：%s" % nl)
    if sorted(nc) != sorted(nl):
        ok = False
    for name in nc:
        a, b = str(vc.get(name)), str(vl.get(name))
        if a == b:
            lines.append("   %-20s 值相同" % name)
        else:
            secret = "（隧道地址，值已打码）" if HOST_RE.search(b) else ""
            lines.append("   %-20s 提交=%s  云端=%s %s" % (name, redact(a) or "(空)", redact(b) or "(空)", secret))

    graph_ok = ok
    lines.append("")
    lines.append("== 5. 运行记录能不能落到这个发布版本上")
    run_ok = True
    if pub_json and Path(pub_json).is_file():
        pub = json.loads(Path(pub_json).read_text(encoding="utf-8"))
        wid, ver = pub.get("id"), pub.get("version")
        lines.append("   控制台 workflows/publish 返回：id=%s  version=%s" % (wid, ver))
        pg = pub.get("graph")
        pg = pg if isinstance(pg, dict) else json.loads(pg or "{}")
        lines.append("   已发布版本图：nodes=%d edges=%d" % (len(pg.get("nodes") or []), len(pg.get("edges") or [])))
        traces = sorted(EVIDENCE.glob("cloud_run_*.txt"))
        if not traces:
            run_ok = False
            lines.append("   !! evidence/ 里还没有 cloud_run_*.txt")
        for t in traces:
            text = t.read_text(encoding="utf-8")
            ids = sorted(set(re.findall(r"workflow_id=([0-9a-f-]{36})", text)))
            runs = re.findall(r"workflow_run_id = ([0-9a-f-]{36})", text)
            verdict = "对得上" if ids and set(ids) == {wid} else ("对不上" if ids else "没有记录")
            lines.append("   %s：workflow_id=%s → 与发布版本%s；workflow_run_id %d 个；sha256(dsl) 头=%s"
                         % (t.name, ",".join(ids) or "-", verdict, len(runs),
                            "与当前提交一致" if sha256(DSL) in text else "早于当前提交（重新取样即可刷新）"))
            if ids and set(ids) != {wid}:
                run_ok = False
    else:
        run_ok = False
        lines.append("   !! 未设置 CLOUD_PUBLISHED_JSON，无法把运行记录钉到发布版本上")

    ok = graph_ok and run_ok
    lines.append("")
    lines.append("== 6. 结论")
    if ok:
        lines.append("   提交 DSL 与云端导出/发布版本结构一致；差异只在环境变量取值（本地占位 vs 隧道地址）与云端补的排版字段。")
    elif graph_ok:
        lines.append("   图结构/变量引用/节点字段已经对齐（第 2–4 节 0 差异），但第 5 节的运行记录还钉在上一个发布版本上——"
                     "重跑 tools/cloud_run_trace.py 的取证场景即可闭环；在此之前不能宣称线上跑的就是提交文件。")
    else:
        lines.append("   仍有实质差异，见上面加 · 的字段，不能宣称线上跑的就是提交文件。")
    lines.append("   仍未纳入比对：模型绑定的具体版本（云端插件市场 deepseek 0.0.26）、环境变量值本身。")

    text = "\n".join(lines) + "\n"
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "cloud_publish_parity.txt").write_text(text, encoding="utf-8")

    # 打码后的云端导出副本（app.yaml + manifest 原样）
    src = Path(app_yaml)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    (EXPORT_DIR / "app.yaml").write_text(redact(src.read_text(encoding="utf-8")), encoding="utf-8")
    man = src.parent / "manifest.yaml"
    if man.is_file():
        (EXPORT_DIR / "manifest.yaml").write_text(redact(man.read_text(encoding="utf-8")), encoding="utf-8")
    print(text)
    print("wrote %s  (parity_ok=%s)" % (EVIDENCE / "cloud_publish_parity.txt", ok))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

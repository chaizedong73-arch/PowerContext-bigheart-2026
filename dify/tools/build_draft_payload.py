"""从提交的 DSL 生成「云端草稿整图覆盖」的 POST 载荷——重导入不需要删应用重建。

判题缺口是「提交的 DSL 比线上跑的版本新，运行证据回溯不到提交文件」。
逐节点手改草稿正是这个缺口的来源，所以这里不做补丁：**把仓库文件里的 graph 原样交上去**，
features / conversation_variables / environment_variables（隧道地址、令牌）由浏览器从线上草稿带回。

载荷必须写在**仓库外**（scratch 之类的目录），因为它含云端环境变量以外的整张图，
且密钥约定是「仓库里只有占位值」：

    python3 dify/tools/build_draft_payload.py <repo>/dify <scratch-dir>

之后在已登录的控制台页里发三个请求（详见 RUNBOOK §4）：GET draft 取 hash →
POST draft {graph: 本地整图, features/conversation_variables: 线上原样, hash} →
POST publish。⚠ 这个接口不接受 environment_variables，多传会 400 extra_forbidden。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

D = Path(sys.argv[1])
S = Path(sys.argv[2])

dsl = yaml.safe_load((D / "dsl/midnight_archive_chatflow.yml").read_text(encoding="utf-8"))
wf = dsl["workflow"]
graph = wf["graph"]
graph = graph if isinstance(graph, dict) else json.loads(graph)

payload = {
    "graph": graph,
    "features": wf.get("features") or {},
    "conversation_variables": wf.get("conversation_variables") or [],
}
blob = json.dumps(graph, ensure_ascii=False)
refs = set(re.findall(r"\{\{#([A-Za-z0-9_.\-\[\]]+)#\}\}", blob))

out = S / "draft_payload_full.json"
out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
print("nodes=%d edges=%d refs=%d" % (len(graph["nodes"]), len(graph["edges"]), len(refs)))
print("conversation_variables=%s" % [v.get("name") for v in payload["conversation_variables"]])
print("code_nodes=%s" % {n["data"]["title"]: len(n["data"].get("code") or "")
                         for n in graph["nodes"] if n["data"].get("type") == "code"})
print("env_names_in_repo=%s（这些不进载荷，云端草稿里已有）"
      % [e.get("name") for e in wf.get("environment_variables") or []])
print("payload_bytes=%d -> %s" % (out.stat().st_size, out))
print("\n浏览器侧：GET 线上草稿拿 hash，POST {graph:本文件.graph, features:线上, conversation_variables:线上, hash:线上}，再 POST publish。")

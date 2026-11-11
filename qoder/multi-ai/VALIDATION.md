# 验证样例：多AI交接桥（Qoder 赛题）

提交材料要求「验证样例：初始数据 + 输入 + 步骤 + 预期结果」，并展示 seekdb / PowerContext 真实发挥作用。本文件即验证样例正文；第 5 节执行记录表在阶段 5 由实际运行结果回填。

## 1. 初始数据

| 文件 | 内容 | 用途 |
| --- | --- | --- |
| `demo-data/sample-corpus.jsonl` | 合成演示语料 12 条（2 个会话、8 条消息、2 条文件操作；虚构项目「校园二手书交易小程序」，数据均不含真实个人信息） | 主验证语料 |
| 用户本机 agent 会话（可选） | 运行 `node ingest/export-corpus.mjs` 从本机 DeepSeek Harness / WorkBuddy / Codex CLI 公开会话导出 | 真实语料扩展 |

## 2. 环境准备（见 README 快速开始）

```bash
bash install.sh                      # venv + 依赖 + 嵌入模型(int8) + 自检
```

## 3. 验证步骤与预期结果

### V1 语料导入 + 检索库建立（seekdb 模式合规演示）

- **输入**：`backend/demo-data/sample-corpus.jsonl`。
- **步骤**：
  ```bash
  "$HOME/handoff-bridge-venv/bin/python" backend/importer.py --corpus demo-data/sample-corpus.jsonl
  ```
- **预期**：返回 `written=12`、`collectionCount=12`、`byKind={message:7, change:3, session:2}`（2026-11-12 实测值）。
- **seekdb 合规跑法**：默认后端为 `light`（零 torch、~470MB 峰值）；设置 `MULTI_AI_STORE=seekdb` 可切换 pyseekdb 嵌入式三路混合检索（HNSW cosine），验证 seekdb 实际参与检索链路。
- **PowerContext 合规跑法**：`backend/dispatch.py` / `backend/pc.py` 走 PowerContext 本地 Server 的 `/v1/memory/*`、`/v1/context/prepare` 端点做跨会话记忆与上下文准备（`POWERCONTEXT_URL` 指向本地 `powercontext server run`）。

### V2 三路混合检索（带出处）

- **输入**：查询「支付 模块」。
- **步骤**：`backend/handoff_brief.py` 内部 `store.hybrid_search`；或启动控制台（`start-bridge.cmd` → http://localhost:8765 ）在检索页查询。
- **预期**：返回 top 结果命中 `demo-s2` 的支付评估消息与 `demo-s1` 的「尚未完成：支付模块还没有接」，每条带 `srcFile:srcLine`（demo-events.jsonl:4 / :9）。

### V3 交接简报（核心功能）

- **输入**：
  ```bash
  "$HOME/handoff-bridge-venv/bin/python" backend/handoff_brief.py \
    --intent "agentB 接手 agentA 的校园二手书交易小程序" \
    --topic "二手书 交易小程序 校园" \
    --src agentA --dst agentB --out /tmp/demo-brief.md
  ```
- **预期**：八节简报——①接手意图（命中 demo-s2 started）②已完成工作（18 项测试/冒烟 5 项）③关键决策（本地存储不上云）④未解决线头（支付沙箱待验证、消息通知未实现——从成功汇报里机械挖出）⑤跨 agent 痕迹（双方）⑥关键路径（demo/src/pay.js 等）⑦相关会话 ⑧时间线；**每条带 demo-events.jsonl 行号**，控制台 `/api/bridge/events/inspect?start=9&limit=1` 可回查原文。
- **验收基线**：本项目按同方法建答案 key 做过硬验收（录音记忆场景 100/100、任务提醒场景 78.9/100），评测套件 3 用例 94.6/100（`backend/run_eval_suite.py` 可复跑）。

### V4 MCP 跨 AI 调用（接手方免启动）

- **输入**：任一 MCP 客户端注册 `get_handoff_brief`（README「MCP 接入」），调用 `{src:"agentA", dst:"agentB", topic:"二手书 交易小程序 校园", intent:"agentB 接手校园二手书小程序"}`。
- **步骤**：客户端发起 tools/call；轻壳 spawn 冷启动 worker（`tools/list` 先行验证）。
- **预期**：约 10-20 秒返回完整简报（首次含模型加载）；调用台账 +1；worker 退出即注销、无残留进程。

## 4. 性能与隐私事实

- 简报全链内存峰值 ~0.5GB（原 pyseekdb+torch 路线 ~1.4GB，-68%）；生成一份简报 5-8 秒。
- 全链路不调用 LLM、不上传数据；采集带 redact 掩码；检索库落本机数据目录。

## 5. 执行记录（阶段 5 回填）

| 用例 | 运行时间 | 结果 | 证据（截图/日志路径） |
| --- | --- | --- | --- |
| V1 导入 | ⬜ | ⬜ | ⬜ |
| V2 检索 | ⬜ | ⬜ | ⬜ |
| V3 简报 | ⬜ | ⬜ | ⬜ |
| V4 MCP | ⬜ | ⬜ | ⬜ |
| seekdb 模式 | ⬜ | ⬜ | ⬜ |
| PowerContext | ⬜ | ⬜ | ⬜ |

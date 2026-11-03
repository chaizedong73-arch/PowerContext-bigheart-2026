# 阶段 3：PowerContext 学习者记忆接入

> 前置：阶段 2 完成。**HTTP 端点以下方清单为准（均已从仓库 `src/powercontext/http/_generated/schema.py` 核实），不要发明其他端点。** 在 Qoder 中执行后自测并截图存档。

---

为「复习教练 RecallLoop」接入 PowerContext，把学习者的薄弱点、偏好与进度存成长期记忆，并实现跨会话续接。seekdb 管资料检索，PowerContext 管学习者记忆，两者不重叠。

## 一、安装并启动 PowerContext Server（手动一次性操作，不由代码完成）

在 WSL2/Linux 终端：

```bash
uv tool install --force "powercontext[cli,server]==1.2.0"
powercontext server run            # 默认监听 http://127.0.0.1:8000
# 另开终端自检：
powercontext doctor
curl http://127.0.0.1:8000/health/live
```

本地默认配置用 SQLite，**显式写入 Memory 不需要配置任何大模型推理提供方**，适合演示。

## 二、已核实的 HTTP API（全部为 POST，JSON）

| 端点 | 请求体 | 说明 |
| --- | --- | --- |
| `POST /v1/scopes` | `{"title": "...", "summary": "...", "idempotency_key": "recallloop"}` | 创建稳定 Scope，响应含 `scope_id`，应用要保存复用 |
| `POST /v1/sources/content` | `{"scope_id", "source_id", "content", "metadata"}` | 留存可追溯的原始观察（如一次自评记录） |
| `POST /v1/memory/remember` | `{"scope_id", "kind": "decision"|"observation", "text": "...", "reason": "..."}` | 显式写入长期记忆 |
| `POST /v1/memory/search` | `{"scope_id", "query": "..."}` | 检索已存记忆 |
| `POST /v1/context/prepare` | `{"scope_id", "query": "...", "max_bytes": 4000}` | 取出有界的历史上下文；响应 `{"status": "ready"|"empty", "content": ...}`，空 Scope 返回 empty 属正常 |

安全规则：启用鉴权时 Token 从环境变量 `POWERCONTEXT_TOKEN` 读取，绝不写进代码/URL/日志。scope_id 由应用决定并保存，绝不允许由模型输出决定。

## 三、实现 `qoder/app/memory.py`

1. `ensure_scope()`：若环境变量 `POWERCONTEXT_SCOPE_ID` 为空，调 `/v1/scopes` 创建（`idempotency_key: "recallloop-app"`），把返回的 `scope_id` 打印到日志并缓存到 `qoder/app/data/scope.json`，重启复用。
2. `record_review(event)`：一次自评结束后：
   - 先 `POST /v1/sources/content` 留底：`source_id = f"review-{时间戳}"`，content 为「问题 + 用户答案 + 自评结果 + 出处引用」；
   - 若自评为「不会/模糊」，再 `POST /v1/memory/remember`，`kind: "observation"`，text 形如「薄弱知识点：<知识点>，来源 <source>，第 N 次答错」；
   - 用户在设置里保存的偏好（每天复习时长、优先方向）用 `kind: "decision"` 写入。
3. `load_context(query)`：调 `/v1/context/prepare`（max_bytes=4000）取回历史上下文；`status == "empty"` 时返回空，不伪造历史。
4. `search_memory(query)`：调 `/v1/memory/search`，供出题时按薄弱点过滤。
5. **记忆日志（重要，用于提交证据）**：每次调用 PowerContext 成功后，向 `qoder/app/data/memory_log.jsonl` 追加一行 JSON：时间、端点、请求摘要（不含 Token）、响应关键字段。

## 四、接线

1. `POST /api/review/answer`（阶段 1 占位）现在真正落库：自评结果先写本地 JSON 文件，再异步调用 `record_review`。
2. `GET /api/plan` 现在真正实现：`load_context("薄弱知识点 复习偏好")` + `search_memory("薄弱")`，结合本地自评记录，生成今日清单：优先薄弱点相关来源 → 次选久未复习的块。响应含 `{"plan": [...], "memory_context_status": "ready"|"empty"}`。

## 五、自测

1. 启动 PowerContext Server 与应用。
2. 提交 3 条自评（1 会、2 不会），确认 `memory_log.jsonl` 出现 sources/content 与 memory/remember 的调用记录。
3. 重启应用进程，打开进度页，确认「上次复习进度」来自 PowerContext 返回的上下文（status=ready）。
4. 用 `powercontext doctor` 或 curl 确认 scope 下确实有记忆条目。

完成后输出：改动文件清单、memory_log.jsonl 样例、跨会话续接的验证截图说明。

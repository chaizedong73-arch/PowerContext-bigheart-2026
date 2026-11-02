#!/usr/bin/env bash
# 端到端演示：初始化 -> 导入代码/规格 -> MiniMax 真实调用改代码 -> 规格更新 -> 再改 -> 全量验证
# 用法: MINIMAX_API_KEY=... bash scripts/run_e2e.sh
# 可用 CTXSDD_EMBEDDER=default|st|hash 强制指定嵌入器（默认自动探测）
set -euo pipefail
cd "$(dirname "$0")/.."

: "${MINIMAX_API_KEY:?请先导出 MINIMAX_API_KEY（MiniMax 开放平台 API Key）}"
PY="${PYTHON:-python}"

echo "=== [0/7] 清点修改前状态 ==="
$PY -m ctxsdd tasks || true

echo "=== [1/7] init（空状态初始化 seekdb 与持久化状态） ==="
$PY -m ctxsdd init

echo "=== [2/7] import-code（业务代码与测试索引进 seekdb） ==="
$PY -m ctxsdd import-code

echo "=== [3/7] import-spec v1 ==="
$PY -m ctxsdd import-spec specs/reading_list_spec_v1.md --version 1

echo "=== [4/7] run-task #1：实现 rating / filter_by_genre / export_csv ==="
echo "--- 目标测试修改前应失败，MiniMax 修改后应全过 ---"
$PY -m ctxsdd run-task \
  --task-file tasks/01_implement_features_v1.md \
  --targets business/reading_list/tests/test_target_features.py

echo "=== [5/7] import-spec v2（规格更新并重新导入） ==="
$PY -m ctxsdd import-spec specs/reading_list_spec_v2.md --version 2

echo "=== [6/7] run-task #2：按 v2 更新（导出排序 + rating 校验） ==="
$PY -m ctxsdd run-task \
  --task-file tasks/02_apply_spec_v2.md \
  --targets business/reading_list/tests/test_spec_update.py

echo "=== [7/7] verify 全量测试 + 读取持久化状态（重启模块后复用） ==="
$PY -m ctxsdd verify

echo
echo "E2E OK —— 证据文件见 docs/runs/（minimax_dev_log.jsonl、ob_query_log.jsonl、"
echo "test_run_log.jsonl、powercontext_state.json、context_pack*.json、code_diff*.txt、spec_update/）"

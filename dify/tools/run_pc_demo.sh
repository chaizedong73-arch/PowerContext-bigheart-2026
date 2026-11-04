#!/usr/bin/env bash
# Exercise the whole pc_memory.py CLI and check DSL/script scope parity.
set -u
D="$(cd "$(dirname "$0")/.." && pwd)"   # 本 dify 目录；脚本可在任意机器上跑
OUT="$D/evidence/pc_memory_run.txt"
PY="${ARCHIVE_PY:-$HOME/recallloop-venv/bin/python}"
mkdir -p "$D/evidence"
cd "$D/powercontext" || exit 1
{
  echo "=== 1. ensure with the same 工号 the DSL used (parity check)"
  "$PY" pc_memory.py ensure "A-VALIDATE-1"
  echo "=== 1b. scope_id captured from the DSL-shaped request"
  grep -o '"scope_id":"[^"]*"' "$D/capture/scopes.json" | head -1
  echo "=== 2. two messy spellings of the same 工号 must reuse ONE scope (idempotency)"
  "$PY" pc_memory.py ensure ' A"22/01 '
  "$PY" pc_memory.py ensure 'A22/01!'
  echo "=== 3. seed a historical episode for a fresh player"
  "$PY" pc_memory.py ensure A-DEMO-7
} > "$OUT" 2>&1
echo "written $OUT"

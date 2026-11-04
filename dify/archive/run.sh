#!/usr/bin/env bash
# 启动《午夜档案馆》seekdb 检索后端（在 Linux x86_64 / WSL2 中运行）
set -euo pipefail

cd "$(dirname "$0")"

if [[ -f .env ]]; then
  set -a; source .env; set +a
fi

export ARCHIVE_SEEKDB_PATH="${ARCHIVE_SEEKDB_PATH:-$HOME/dify-archive-data/seekdb}"
export ARCHIVE_PORT="${ARCHIVE_PORT:-8011}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
PY="${ARCHIVE_PY:-$HOME/recallloop-venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3)"

# 首次运行（或数据目录为空）时自动灌库
if [[ ! -d "$ARCHIVE_SEEKDB_PATH" || ! -f .seeded ]]; then
  "$PY" seed.py
  touch .seeded
fi

echo "[archive] token protected = $([[ -n "${ARCHIVE_TOKEN:-}" ]] && echo yes || echo no)"
echo "[archive] listening on 0.0.0.0:${ARCHIVE_PORT}"
exec "$PY" -m uvicorn main:app --host 0.0.0.0 --port "$ARCHIVE_PORT"

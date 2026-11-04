"""把原创怪谈档案灌入 seekdb（建集合 → embed → upsert → 打印统计）。

用法：``python seed.py`` （需先 source .env 或按 README 设置环境变量）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import seekdb_store as store  # noqa: E402


def main() -> int:
    if not store.DATA_FILE.exists():
        print(f"缺少档案语料：{store.DATA_FILE}", file=sys.stderr)
        return 2
    print(json.dumps(store.seed(recreate=True), ensure_ascii=False, indent=2))
    print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

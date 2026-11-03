"""pytest 路径配置：让测试可以直接 import reading_list。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

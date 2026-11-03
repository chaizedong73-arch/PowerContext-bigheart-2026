"""集中配置：全部敏感项只从环境变量读取，代码与仓库不含任何密钥。"""

import os
from pathlib import Path

# pyseekdb 默认 embedding function 会尝试联网下载 HF 模型；我们自带向量、从不用它，
# 强制离线避免每次建集合时长达数十秒的重试（做法参照 minimax/ctxsdd/obstore.py）。
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

APP_DIR = Path(__file__).resolve().parent

# seekdb 嵌入式数据库目录。注意：必须位于本地文件系统，不能放 /mnt/c 等挂载路径。
SEEKDB_PATH = Path(os.environ.get("SEEKDB_PATH", str(APP_DIR / "data" / "seekdb")))

# 运行时数据目录（日志、scope、自评记录）。默认与 seekdb 同级。
DATA_DIR = Path(os.environ.get("RECALLLOOP_DATA_DIR", str(APP_DIR / "data")))

# PowerContext Server
POWERCONTEXT_URL = os.environ.get("POWERCONTEXT_URL", "http://127.0.0.1:8000").rstrip("/")
POWERCONTEXT_SCOPE_ID = os.environ.get("POWERCONTEXT_SCOPE_ID", "")  # 可为空，空则自动创建
POWERCONTEXT_TOKEN = os.environ.get("POWERCONTEXT_TOKEN", "")  # 仅进程环境，绝不落盘/入日志
POWERCONTEXT_TIMEOUT = float(os.environ.get("POWERCONTEXT_TIMEOUT", "8"))

# Embedding
EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "local")  # local | api
# 维度须与 pyseekdb 默认 embedding function 一致（384），create_collection 会校验。
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "384"))
EMBEDDING_API_KEY = os.environ.get("EMBEDDING_API_KEY", "")
EMBEDDING_API_BASE = os.environ.get("EMBEDDING_API_BASE", "")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "")

# 检索参数
VECTOR_CANDIDATES = int(os.environ.get("VECTOR_CANDIDATES", "12"))
ASK_TOP_K = int(os.environ.get("ASK_TOP_K", "5"))
KEYWORD_WEIGHT = float(os.environ.get("KEYWORD_WEIGHT", "0.4"))

COLLECTION_NAME = "review_docs"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SEEKDB_PATH.parent.mkdir(parents=True, exist_ok=True)

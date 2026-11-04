"""多AI协同 v2 · 集中配置：全部敏感项只从环境变量读取，代码与仓库不含任何密钥。

镜像 RecallLoop（qoder/app/config.py）已验证的约定：
- pyseekdb 默认 embedding function 会联网下载 HF 模型；我们自带向量、从不用它，强制离线。
- seekdb 嵌入式数据库目录必须位于本地文件系统，不能放 /mnt/c 等挂载路径
  （本机仓库在 /mnt/c 下，故默认落到 WSL 原生 ~/multi-ai-data/seekdb）。
- PowerContext token 仅进程环境，绝不落盘/入日志。
"""

import os
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

BACKEND_DIR = Path(__file__).resolve().parent

# 运行时数据目录（seekdb、日志、scope）。默认 WSL 原生 home，避开 /mnt/c。
DATA_DIR = Path(os.environ.get("MULTI_AI_DATA_DIR", str(Path.home() / "multi-ai-data")))
SEEKDB_PATH = Path(os.environ.get("SEEKDB_PATH", str(DATA_DIR / "seekdb")))

# 语料 JSONL（Node ingest 导出）。默认指向仓库内 ingest/out/corpus.jsonl（读 /mnt/c 没问题，
# 只有 seekdb 数据目录不能放 /mnt/c）。可用 --corpus 或环境变量覆盖。
_DEFAULT_CORPUS = BACKEND_DIR.parent / "ingest" / "out" / "corpus.jsonl"
CORPUS_PATH = Path(os.environ.get("MULTI_AI_CORPUS", str(_DEFAULT_CORPUS)))

# PowerContext Server（本地，无云）
POWERCONTEXT_URL = os.environ.get("POWERCONTEXT_URL", "http://127.0.0.1:8000").rstrip("/")
POWERCONTEXT_SCOPE_ID = os.environ.get("POWERCONTEXT_SCOPE_ID", "")  # 空则自动创建；scope 由应用决定
POWERCONTEXT_TOKEN = os.environ.get("POWERCONTEXT_TOKEN", "")  # 仅进程环境，绝不落盘/入日志
POWERCONTEXT_TIMEOUT = float(os.environ.get("POWERCONTEXT_TIMEOUT", "8"))

# Embedding
EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "local")  # local | api
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "384"))  # 须与 pyseekdb 默认 EF 维度一致
EMBEDDING_API_KEY = os.environ.get("EMBEDDING_API_KEY", "")
EMBEDDING_API_BASE = os.environ.get("EMBEDDING_API_BASE", "")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "")

# 检索参数
VECTOR_CANDIDATES = int(os.environ.get("VECTOR_CANDIDATES", "48"))
SEARCH_TOP_K = int(os.environ.get("SEARCH_TOP_K", "8"))
KEYWORD_WEIGHT = float(os.environ.get("KEYWORD_WEIGHT", "0.4"))

COLLECTION_NAME = os.environ.get("MULTI_AI_COLLECTION", "multi_ai_corpus")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SEEKDB_PATH.parent.mkdir(parents=True, exist_ok=True)

"""任务状态持久化：所有命令跨进程读写同一份状态，重启模块后可复用。

状态文件位于 .ctxsdd/powercontext_state.json；每次保存同时镜像一份到
docs/runs/powercontext_state.json 作为交付证据。
"""

import json
import time
from pathlib import Path


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class State:
    """ctxsdd 全局状态：初始化信息、规格版本、任务记录。"""

    def __init__(self, path: Path, mirror_path: Path | None = None):
        self.path = Path(path)
        self.mirror_path = Path(mirror_path) if mirror_path else None
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            self.loaded_from_existing = True
        else:
            self.data = {
                "initialized_at": None,
                "embedder": None,
                "specs": {},
                "code_index": {},
                "tasks": [],
            }
            self.loaded_from_existing = False

    @property
    def initialized(self) -> bool:
        return bool(self.data.get("initialized_at"))

    def mark_initialized(self, embedder: str) -> None:
        if not self.data.get("initialized_at"):
            self.data["initialized_at"] = _now()
        self.data["embedder"] = embedder

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if self.mirror_path is not None:
            self.mirror_path.parent.mkdir(parents=True, exist_ok=True)
            self.mirror_path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

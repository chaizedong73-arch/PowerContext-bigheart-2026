"""规格文档切块：按 Markdown 标题把规格切成带来源与版本的块，供向量索引入库。"""

import hashlib
import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass
class SpecChunk:
    chunk_id: str
    source: str      # 规格名，如 reading_list_spec
    version: int     # 规格版本号
    heading: str     # 所在小节标题
    text: str
    index: int


def chunk_spec(text: str, source: str, version: int) -> list[SpecChunk]:
    """按标题切分 Markdown 规格；每个块带稳定 chunk_id（内容哈希）。"""
    lines = text.splitlines()
    sections: list[tuple[str, list[str]]] = []
    cur_heading = "(前言)"
    cur: list[str] = []
    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            if cur:
                sections.append((cur_heading, cur))
            cur_heading = m.group(2).strip()
            cur = [line]
        else:
            cur.append(line)
    if cur:
        sections.append((cur_heading, cur))

    chunks: list[SpecChunk] = []
    for i, (heading, body_lines) in enumerate(sections):
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        digest = hashlib.md5(body.encode("utf-8")).hexdigest()[:8]
        chunks.append(
            SpecChunk(
                chunk_id=f"spec_v{version}_{i:02d}_{digest}",
                source=source,
                version=version,
                heading=heading,
                text=body,
                index=i,
            )
        )
    return chunks

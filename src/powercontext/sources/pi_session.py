# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Pi session branch capture with immutable events and incremental checkpoints."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import ClassVar
from urllib.parse import quote

from pydantic import BaseModel, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    TextEvidence,
)
from powercontext.sources.models import Source, SourceMaterialization

PI_EVENT_SOURCE_NAME = "pi-event"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
_CONNECTOR_VERSION = "1"
_PI_IDENTITY_PREFIX = "pi:"
_SUMMARY_EVENT_TYPES = frozenset({"compaction", "branch_summary"})
_CHECKPOINT_VERSION = 1


class PiSessionInput(BaseModel):
    """Complete offline byte content of one Pi session JSONL archive."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event on the selected branch with its complete input record."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict


def parse_pi_session(value: PiSessionInput, /) -> tuple[PiEventInput, ...]:
    """Parse the selected branch of a Pi v3 session archive, or fail atomically.

    The archive must be UTF-8 (a leading BOM is accepted) JSONL whose first
    record declares ``type=session`` with ``version=3`` and a non-empty id.
    Every later record needs a unique id whose parent is empty or already
    seen, which rules out cycles by construction. Control nodes keep their
    graph links but produce no text evidence; compaction and branch summaries
    never erase the evidence captured before them.
    """

    session_id, nodes = _parse_records(value.content)
    leaf_id = value.leaf_id if value.leaf_id is not None else _last_node_id(nodes)
    events = []
    for record in _selected_chain(nodes, leaf_id):
        event = _visible_event(session_id, record)
        if event is not None:
            events.append(event)
    return tuple(events)


def _parse_records(content: bytes, /) -> tuple[str, dict[str, dict]]:
    """Decode the whole archive first and index every record by its id."""

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("session content must be valid UTF-8") from error  # noqa: TRY003
    records = _decode_jsonl(text)
    header = records[0] if records else None
    if not isinstance(header, dict) or header.get("type") != "session":
        raise ValueError("the first record must declare a session header")  # noqa: TRY003
    version = header.get("version")
    if isinstance(version, bool) or version != 3:
        raise ValueError("session header must declare version 3")  # noqa: TRY003
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session header must carry a non-empty id")  # noqa: TRY003
    nodes: dict[str, dict] = {}
    for record in records[1:]:
        _register_node(record, nodes)
    return session_id, nodes


def _decode_jsonl(text: str, /) -> list[object]:
    records: list[object] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise ValueError("every session line must be valid JSON") from error  # noqa: TRY003
    return records


def _register_node(record: object, nodes: dict[str, dict], /) -> None:
    if not isinstance(record, dict):
        raise ValueError("every session record must be a JSON object")  # noqa: TRY003
    event_id = record.get("id")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("every session record needs a non-empty id")  # noqa: TRY003
    if event_id in nodes:
        raise ValueError("session record ids must be unique")  # noqa: TRY003
    if "parentId" not in record:
        raise ValueError("every session record must declare parentId")  # noqa: TRY003
    parent_id = record["parentId"]
    if parent_id is not None and not isinstance(parent_id, str):
        raise ValueError("parentId must be a string or null")  # noqa: TRY003
    if parent_id and parent_id not in nodes:
        raise ValueError("parentId must reference an earlier record")  # noqa: TRY003
    nodes[event_id] = record


def _last_node_id(nodes: dict[str, dict], /) -> str:
    if not nodes:
        raise ValueError("session must contain at least one record")  # noqa: TRY003
    return next(reversed(nodes))


def _selected_chain(nodes: dict[str, dict], leaf_id: object, /) -> tuple[dict, ...]:
    """Walk parent links from the selected leaf back to the root, root first."""

    if not isinstance(leaf_id, str) or leaf_id not in nodes:
        raise ValueError("leaf_id must select one session record")  # noqa: TRY003
    chain = []
    current = leaf_id
    while current:
        record = nodes[current]
        chain.append(record)
        parent = record.get("parentId")
        current = parent if isinstance(parent, str) else None
    chain.reverse()
    return tuple(chain)


def _visible_event(session_id: str, record: dict, /) -> PiEventInput | None:
    """Build one visible event, or return None for control-only records."""

    event_type = record.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise ValueError("every session record needs a type")  # noqa: TRY003
    if event_type == "message":
        role, body = _message_body(record)
    elif event_type in _SUMMARY_EVENT_TYPES:
        role, body = event_type, _summary_body(record)
    elif event_type == "custom_message":
        role, body = "custom", _blocks_body(record.get("content"), allow_missing=True)
    else:
        return None
    if not body:
        return None
    return PiEventInput(
        session_id=session_id,
        event_id=record["id"],
        parent_id=record.get("parentId"),
        event_type=event_type,
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def _message_body(record: dict, /) -> tuple[str, str]:
    message = record.get("message")
    if not isinstance(message, dict):
        raise ValueError("message records must contain a message object")  # noqa: TRY003
    role = message.get("role")
    if not isinstance(role, str) or not role:
        raise ValueError("message records must declare a role")  # noqa: TRY003
    if role == "bashExecution":
        return role, _bash_body(message)
    content = message.get("content")
    if role == "toolResult":
        return role, _tool_result_body(message, content)
    if isinstance(content, str):
        return role, content
    if isinstance(content, list):
        return role, _blocks_body(content)
    raise ValueError("message content must be a string or a list of blocks")  # noqa: TRY003


def _bash_body(message: dict, /) -> str:
    command = message.get("command")
    output = message.get("output")
    if not isinstance(command, str) or not isinstance(output, str):
        raise ValueError("bashExecution records need string command and output")  # noqa: TRY003
    return f"$ {command}\n{output}"


def _tool_result_body(message: dict, content: object, /) -> str:
    tool_name = message.get("toolName")
    tool_call_id = message.get("toolCallId")
    if not isinstance(tool_name, str) or not tool_name or not isinstance(tool_call_id, str) or not tool_call_id:
        raise ValueError("toolResult records need toolName and toolCallId")  # noqa: TRY003
    return f"[tool_result {tool_name} {tool_call_id}] {_result_text(content)}"


def _result_text(content: object, /) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(_text_value(block))
        return "\n".join(parts)
    raise ValueError("toolResult content must be a string or a list of blocks")  # noqa: TRY003


def _blocks_body(content: object, /, *, allow_missing: bool = False) -> str:
    """Join the visible string and text-block parts of one content value."""

    if content is None and allow_missing:
        return ""
    if not isinstance(content, list):
        raise ValueError("content must be a list of blocks")  # noqa: TRY003
    parts = []
    for block in content:
        if not isinstance(block, dict):
            raise ValueError("every content block must be a JSON object")  # noqa: TRY003
        block_type = block.get("type")
        if block_type == "text":
            parts.append(_text_value(block))
        elif block_type == "toolCall":
            parts.append(_tool_call_body(block))
    return "\n".join(parts)


def _text_value(block: dict, /) -> str:
    text = block.get("text")
    if not isinstance(text, str):
        raise ValueError("text blocks must carry string text")  # noqa: TRY003
    return text


def _tool_call_body(block: dict, /) -> str:
    name = block.get("name")
    call_id = block.get("id")
    if not isinstance(name, str) or not name or not isinstance(call_id, str) or not call_id:
        raise ValueError("toolCall blocks need name and id")  # noqa: TRY003
    arguments = block.get("arguments")
    encoded = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"[tool_call {name} {call_id}] {encoded}"


def _summary_body(record: dict, /) -> str:
    summary = record.get("summary")
    if not isinstance(summary, str):
        raise ValueError("summary records must carry a string summary")  # noqa: TRY003
    return summary


def pi_event_source_id(session_id: str, event_id: str, /) -> str:
    """Build the collision-free event identity from the session and event ids."""

    if not isinstance(session_id, str) or not session_id or not isinstance(event_id, str) or not event_id:
        raise ValueError("session and event ids must be non-empty strings")  # noqa: TRY003
    identity = f"{_PI_IDENTITY_PREFIX}{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"source identity must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return identity


def _record_digest(record: dict, /) -> str:
    """Canonical digest of the complete input record for rewrite detection."""

    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _decode_checkpoint(checkpoint: JsonValue | None, /) -> dict[str, str]:
    """Restore imported event identities with their original record digests."""

    if checkpoint is None:
        return {}
    events = checkpoint.get("events") if isinstance(checkpoint, dict) else None
    if not isinstance(events, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in events.items()
    ):
        raise ValueError("checkpoint must encode imported event digests")  # noqa: TRY003
    return dict(events)


class PiEventSource(Source):
    """Persisted evidence for one visible Pi session event."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict


class PiEventSourceAdapter:
    """Resolve and read the event-level Pi Source."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        return PiEventSource(
            name=pi_event_source_id(value.session_id, value.event_id),
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Project one event as canonical text evidence without rewriting the body."""

    name: ClassVar[str] = TEXT_EVIDENCE_PROJECTION_KEY.name
    version: ClassVar[str] = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: ClassVar[type[BaseModel]] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_ADAPTER = PiEventSourceAdapter()
PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PI_EVENT_SOURCE_ADAPTER,
    projections=(PiEventTextEvidenceProjection(),),
)


class PiSessionConnector:
    """Incrementally capture the selected branch of one Pi session file."""

    name = PI_SESSION_CONNECTOR_NAME
    version = _CONNECTOR_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        """Parse the whole file first, then submit only the unimported events.

        The checkpoint stores every imported identity with the digest of its
        original record, so restarts deduplicate and any rewrite of an
        already-imported event raises before new evidence is submitted.
        """

        raw = self._path.read_bytes()
        events = parse_pi_session(PiSessionInput(content=raw, leaf_id=self._leaf_id))
        imported = _decode_checkpoint(session.checkpoint)
        digests = {pi_event_source_id(event.session_id, event.event_id): _record_digest(event.record) for event in events}
        for identity, digest in digests.items():
            known = imported.get(identity)
            if known is not None and known != digest:
                raise ValueError("an imported event was rewritten")  # noqa: TRY003
        for event in events:
            identity = pi_event_source_id(event.session_id, event.event_id)
            if identity in imported:
                continue
            await session.submit(identity, PI_EVENT_SOURCE_NAME, event)
            imported[identity] = digests[identity]
        checkpoint = {"version": _CHECKPOINT_VERSION, "events": imported}
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint=checkpoint)

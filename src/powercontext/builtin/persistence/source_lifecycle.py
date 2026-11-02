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

"""Durable Source lifecycle graphs: registered nodes and committed events."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_EVENTS_TABLE, SOURCE_LIFECYCLE_NODES_TABLE

__all__ = ["LifecycleEventRow", "LifecycleNodeRow", "SourceLifecycleRepository"]


@dataclass(frozen=True, slots=True)
class LifecycleNodeRow:
    """One registered lifecycle node and its current availability."""

    scope_id: str
    node_id: str
    kind: str
    policy: str | None
    revision: int | None
    parents: tuple[str, ...]
    reference: bytes
    state: str


@dataclass(frozen=True, slots=True)
class LifecycleEventRow:
    """One committed lifecycle event for a Source node."""

    scope_id: str
    node_id: str
    sequence: int
    revision: int
    available: bool
    reason: str


class SourceLifecycleRepository:
    """Read and write lifecycle nodes and events within caller transactions."""

    async def register_node(self, connection: AsyncConnection, node: LifecycleNodeRow, /) -> None:
        await connection.execute(
            SOURCE_LIFECYCLE_NODES_TABLE.insert().values(
                scope_id=node.scope_id,
                node_id=node.node_id,
                kind=node.kind,
                policy=node.policy,
                revision=node.revision,
                parents=stored_bytes_list(node.parents),
                reference=node.reference,
                state=node.state,
            )
        )

    async def get_node(self, connection: AsyncConnection, scope_id: str, node_id: str, /) -> LifecycleNodeRow | None:
        row = (
            await connection.execute(
                select(
                    SOURCE_LIFECYCLE_NODES_TABLE.c.kind,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.policy,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.revision,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.parents,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.reference,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.state,
                ).where(
                    SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node_id,
                )
            )
        ).first()
        if row is None:
            return None
        kind, policy, revision, parents, reference, state = row
        return LifecycleNodeRow(
            scope_id=scope_id,
            node_id=node_id,
            kind=kind,
            policy=policy,
            revision=revision,
            parents=load_bytes_list(parents),
            reference=reference,
            state=state,
        )

    async def load_nodes(self, connection: AsyncConnection, scope_id: str, /) -> list[LifecycleNodeRow]:
        rows = (
            await connection.execute(
                select(
                    SOURCE_LIFECYCLE_NODES_TABLE.c.node_id,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.kind,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.policy,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.revision,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.parents,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.reference,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.state,
                )
                .where(SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id)
                .order_by(SOURCE_LIFECYCLE_NODES_TABLE.c.node_id)
            )
        ).all()
        return [
            LifecycleNodeRow(
                scope_id=scope_id,
                node_id=node_id,
                kind=kind,
                policy=policy,
                revision=revision,
                parents=load_bytes_list(parents),
                reference=reference,
                state=state,
            )
            for node_id, kind, policy, revision, parents, reference, state in rows
        ]

    async def record_event(self, connection: AsyncConnection, event: LifecycleEventRow, /) -> None:
        await connection.execute(
            SOURCE_LIFECYCLE_EVENTS_TABLE.insert().values(
                scope_id=event.scope_id,
                node_id=event.node_id,
                sequence=event.sequence,
                revision=event.revision,
                available=event.available,
                reason=event.reason,
            )
        )

    async def latest_events(self, connection: AsyncConnection, scope_id: str, /) -> dict[str, LifecycleEventRow]:
        """Return the highest-sequence committed event per Source node."""

        rows = (
            await connection.execute(
                select(
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.node_id,
                    func.max(SOURCE_LIFECYCLE_EVENTS_TABLE.c.sequence),
                )
                .where(SOURCE_LIFECYCLE_EVENTS_TABLE.c.scope_id == scope_id)
                .group_by(SOURCE_LIFECYCLE_EVENTS_TABLE.c.node_id)
            )
        ).all()
        latest: dict[str, LifecycleEventRow] = {}
        for node_id, sequence in rows:
            event = (
                await connection.execute(
                    select(
                        SOURCE_LIFECYCLE_EVENTS_TABLE.c.revision,
                        SOURCE_LIFECYCLE_EVENTS_TABLE.c.available,
                        SOURCE_LIFECYCLE_EVENTS_TABLE.c.reason,
                    ).where(
                        SOURCE_LIFECYCLE_EVENTS_TABLE.c.scope_id == scope_id,
                        SOURCE_LIFECYCLE_EVENTS_TABLE.c.node_id == node_id,
                        SOURCE_LIFECYCLE_EVENTS_TABLE.c.sequence == sequence,
                    )
                )
            ).first()
            revision, available, reason = event
            latest[node_id] = LifecycleEventRow(
                scope_id=scope_id,
                node_id=node_id,
                sequence=sequence,
                revision=revision,
                available=bool(available),
                reason=reason,
            )
        return latest

    async def update_state(self, connection: AsyncConnection, scope_id: str, node_id: str, state: str, /) -> None:
        await connection.execute(
            update(SOURCE_LIFECYCLE_NODES_TABLE)
            .where(SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id, SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node_id)
            .values(state=state)
        )


def stored_bytes_list(values: tuple[str, ...], /) -> bytes:
    """Persist an ordered string tuple as canonical JSON."""

    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":")).encode()


def load_bytes_list(payload: bytes, /) -> tuple[str, ...]:
    return tuple(json.loads(payload))

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

"""Public Source lifecycle graphs binding persisted Sources to native artifacts.

Sources gain a withdrawable availability state. Registered Memory entries and
Topic Memory artifacts follow their Source parents through an all/any policy
graph: withdrawn evidence deactivates the Memory entries that were generated
from it and excludes Topic Memory artifacts from search, while exact reads and
audit purposes keep working. Availability is durable, per scope, and survives
restarts; replaying committed events never creates new native revisions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import Memory, MemoryCitation, MemoryEntryVersion, MemoryService
from powercontext.builtin.artifacts.memory.errors import MemoryEntryNotFoundError
from powercontext.builtin.persistence.codec import dump_model, load_model
from powercontext.builtin.persistence.source_lifecycle import (
    LifecycleEventRow,
    LifecycleNodeRow,
    SourceLifecycleRepository,
)
from powercontext.builtin.records import BaseValueNotFoundError
from powercontext.errors import ArtifactNotFoundError
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from collections.abc import AsyncConnection

    from powercontext.builtin.runtime.application import BuiltinRuntime

__all__ = ["SourceLifecycleError", "SourceLifecycleEvent", "SourceLifecycleHandle", "ScopedSourceLifecycle"]

_PURPOSES = ("generation", "audit")


class SourceLifecycleError(Exception):
    """A lifecycle registration, event, or resolution was rejected."""


class SourceLifecycleEvent(BaseModel):
    """One committed availability change for a registered Source node."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    revision: int = Field(strict=True, ge=1)
    sequence: int = Field(strict=True, ge=1)
    available: bool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def _require_trimmed_text(cls, value: str) -> str:
        if not value or value.strip() != value:
            raise ValueError("value must be nonempty and trimmed")
        return value


class SourceLifecycleHandle:
    """Public entry point for Source lifecycle graphs."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        return ScopedSourceLifecycle(self._runtime, scope_id)


class ScopedSourceLifecycle:
    """Register lifecycle nodes, commit events, and resolve availability."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self._database = runtime._provider.database
        self.scope_id = scope_id

    async def register_source(
        self,
        node_id: str,
        source_ref: SourceRef,
        /,
        *,
        revision: int,
    ) -> None:
        """Bind one persisted Source identity as the graph's Source node."""

        _validate_node_id(node_id)
        if type(revision) is not int or revision < 1:
            raise SourceLifecycleError("revision must be a positive integer")
        if not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("source_ref must be a persisted Source reference")
        runtime = self._runtime
        async with runtime._context(self.scope_id) as context:
            try:
                await runtime._records().get_source(self.scope_id, source_ref.source_type, source_ref.source_id)
            except BaseValueNotFoundError:
                raise SourceLifecycleError("source_ref must match a persisted Source") from None
            async with runtime._locked(self.scope_id):
                async with self._database.transaction() as connection:
                    await self._insert_node(
                        connection,
                        LifecycleNodeRow(
                            scope_id=self.scope_id,
                            node_id=node_id,
                            kind="source",
                            policy=None,
                            revision=revision,
                            parents=(),
                            reference=dump_model(source_ref, kind="lifecycle-reference", name=node_id),
                            state="active",
                        ),
                    )

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        /,
        *,
        parents: tuple[str, ...],
        policy: Literal["all", "any"],
    ) -> None:
        """Bind one Memory entry citation that follows the Source parents."""

        _validate_node_id(node_id)
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError("citation must be a MemoryCitation")
        runtime = self._runtime
        async with runtime._context(self.scope_id) as context:
            service = context.artifacts.memory
            current = await _head_or_none(service, context.artifacts.memory_artifact_id)
            if current is None:
                raise SourceLifecycleError("scope has no current Memory to cite")
            if citation.memory_ref.artifact_id != context.artifacts.memory_artifact_id:
                raise SourceLifecycleError("citation must reference the scope's own Memory")
            try:
                cited = await service.revision(citation.memory_ref)
            except ArtifactNotFoundError:
                raise SourceLifecycleError("citation must reference a persisted Memory revision") from None
            try:
                await _cited_entry(service, cited, citation)
            except MemoryEntryNotFoundError:
                raise SourceLifecycleError("citation must reference a persisted Memory entry") from None
            async with runtime._locked(self.scope_id):
                state = await self._register_derived(
                    context,
                    node_id,
                    kind="memory",
                    parents=parents,
                    policy=policy,
                    reference=citation,
                )
                if state == "inactive":
                    await self._sync_memory_nodes(context)

    async def register_topic(
        self,
        node_id: str,
        reference: ArtifactRef,
        /,
        *,
        parents: tuple[str, ...],
        policy: Literal["all", "any"],
    ) -> None:
        """Bind one Topic Memory artifact that follows the Source parents."""

        _validate_node_id(node_id)
        if not isinstance(reference, ArtifactRef):
            raise SourceLifecycleError("reference must be a Topic Memory ArtifactRef")
        if reference.family != "topic-memory":
            raise SourceLifecycleError("topic nodes must reference topic-memory artifacts")
        runtime = self._runtime
        async with runtime._context(self.scope_id) as context:
            try:
                await runtime._records().get_artifact_revision(
                    self.scope_id,
                    reference.family,
                    reference.artifact_id,
                    reference.revision,
                )
            except BaseValueNotFoundError:
                raise SourceLifecycleError("reference must match a persisted Topic Memory revision") from None
            async with runtime._locked(self.scope_id):
                await self._register_derived(
                    context,
                    node_id,
                    kind="topic",
                    parents=parents,
                    policy=policy,
                    reference=reference,
                )

    async def apply(self, event: SourceLifecycleEvent, /) -> list[str]:
        """Commit one Source availability event and propagate the graph.

        Replaying an already committed event is idempotent and still reconciles
        any native Memory state left behind by an interrupted propagation.
        """

        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("event must be a SourceLifecycleEvent")
        runtime = self._runtime
        async with runtime._context(self.scope_id) as context:
            async with runtime._locked(self.scope_id):
                repository = SourceLifecycleRepository()
                async with self._database.transaction() as connection:
                    node = await repository.get_node(connection, self.scope_id, event.node_id)
                    if node is None or node.kind != "source":
                        raise SourceLifecycleError("events apply only to registered Source nodes")
                    if node.revision != event.revision:
                        raise SourceLifecycleError("event revision does not match the registered Source revision")
                    latest = await repository.latest_events(connection, self.scope_id)
                    replay = False
                    if event.node_id in latest:
                        last = latest[event.node_id]
                        if event.sequence <= last.sequence:
                            if event.sequence == last.sequence and (
                                last.revision,
                                last.available,
                                last.reason,
                            ) == (event.revision, event.available, event.reason):
                                replay = True
                            else:
                                raise SourceLifecycleError(
                                    "event sequence must strictly increase beyond committed events"
                                )
                    nodes = await repository.load_nodes(connection, self.scope_id)
                    if not replay:
                        row = LifecycleEventRow(
                            scope_id=self.scope_id,
                            node_id=event.node_id,
                            sequence=event.sequence,
                            revision=event.revision,
                            available=event.available,
                            reason=event.reason,
                        )
                        latest[event.node_id] = row
                        await repository.record_event(connection, row)
                    states = _evaluate(nodes, latest)
                    for item in nodes:
                        target = "active" if states[item.node_id] else "inactive"
                        if item.state != target:
                            await repository.update_state(connection, self.scope_id, item.node_id, target)
                await self._sync_memory_nodes(context)
                return sorted(node_id for node_id, available in states.items() if available)

    async def resolve(
        self,
        node_id: str,
        /,
        *,
        purpose: Literal["generation", "audit"],
    ) -> SourceRef | MemoryCitation | ArtifactRef:
        """Return the node's original reference for generation or audit use."""

        if purpose not in _PURPOSES:
            raise SourceLifecycleError("purpose must be 'generation' or 'audit'")
        node = await self._load_node(node_id)
        if node is None:
            raise SourceLifecycleError("node_id is not registered in this scope")
        if purpose == "generation" and node.state != "active":
            raise SourceLifecycleError("node is not available for generation")
        if node.kind == "source":
            return load_model(SourceRef, node.reference, kind="lifecycle-reference", name=node_id)
        if node.kind == "memory":
            return load_model(MemoryCitation, node.reference, kind="lifecycle-reference", name=node_id)
        return load_model(ArtifactRef, node.reference, kind="lifecycle-reference", name=node_id)

    async def inactive_topic_refs(self) -> frozenset[ArtifactRef]:
        """Return Topic Memory references currently excluded from search."""

        async with self._runtime._context(self.scope_id):
            async with self._database.transaction() as connection:
                nodes = await SourceLifecycleRepository().load_nodes(connection, self.scope_id)
        return frozenset(
            load_model(ArtifactRef, node.reference, kind="lifecycle-reference", name=node.node_id)
            for node in nodes
            if node.kind == "topic" and node.state == "inactive"
        )

    async def _load_node(self, node_id: str, /) -> LifecycleNodeRow | None:
        async with self._runtime._context(self.scope_id):
            async with self._database.transaction() as connection:
                return await SourceLifecycleRepository().get_node(connection, self.scope_id, node_id)

    async def _register_derived(
        self,
        context,
        node_id: str,
        /,
        *,
        kind: str,
        parents: tuple[str, ...],
        policy: str,
        reference: MemoryCitation | ArtifactRef,
    ) -> str:
        """Validate and persist one derived node; return its initial state."""

        if policy not in ("all", "any"):
            raise SourceLifecycleError("policy must be 'all' or 'any'")
        if not parents:
            raise SourceLifecycleError("derived nodes require at least one parent")
        if len(set(parents)) != len(parents):
            raise SourceLifecycleError("parents must not repeat")
        if node_id in parents:
            raise SourceLifecycleError("nodes cannot be their own parent")
        async with self._database.transaction() as connection:
            nodes = await SourceLifecycleRepository().load_nodes(connection, self.scope_id)
            by_id = {node.node_id: node for node in nodes}
            if node_id in by_id:
                raise SourceLifecycleError("node_id is already registered in this scope")
            parent_states: dict[str, str] = {}
            for parent in parents:
                item = by_id.get(parent)
                if item is None:
                    raise SourceLifecycleError("parents must be registered before the derived node")
                parent_states[parent] = item.state
            for parent in parents:
                if node_id in _ancestors(parent, by_id):
                    raise SourceLifecycleError("registrations must not create reference cycles")
            if policy == "all":
                available = all(state == "active" for state in parent_states.values())
            else:
                available = any(state == "active" for state in parent_states.values())
            state = "active" if available else "inactive"
            await SourceLifecycleRepository().register_node(
                connection,
                LifecycleNodeRow(
                    scope_id=self.scope_id,
                    node_id=node_id,
                    kind=kind,
                    policy=policy,
                    revision=None,
                    parents=parents,
                    reference=dump_model(reference, kind="lifecycle-reference", name=node_id),
                    state=state,
                ),
            )
        return state

    async def _insert_node(self, connection: AsyncConnection, node: LifecycleNodeRow, /) -> None:
        """Insert one node, rejecting duplicate identities."""

        repository = SourceLifecycleRepository()
        existing = await repository.get_node(connection, self.scope_id, node.node_id)
        if existing is not None:
            raise SourceLifecycleError("node_id is already registered in this scope")
        await repository.register_node(connection, node)

    async def _sync_memory_nodes(self, context, /) -> None:
        """Reconcile native Memory entry states with lifecycle availability.

        Safe to run repeatedly: entries already in the target state never gain
        a new Memory revision, so replays after interrupted propagation only
        complete the missing transitions.
        """

        service = context.artifacts.memory
        async with self._database.transaction() as connection:
            nodes = [
                node
                for node in await SourceLifecycleRepository().load_nodes(connection, self.scope_id)
                if node.kind == "memory"
            ]
        for node in nodes:
            citation = load_model(MemoryCitation, node.reference, kind="lifecycle-reference", name=node.node_id)
            if citation.memory_ref.artifact_id != context.artifacts.memory_artifact_id:
                continue
            memory = await _head_or_none(service, context.artifacts.memory_artifact_id)
            if memory is None:
                return
            manifest = {item.entry_id: item for item in memory.content.manifest.entries}
            item = manifest.get(citation.entry_id)
            if item is None or item.state == node.state:
                continue
            try:
                entry = await service.validate_citation(
                    citation.model_copy(update={"memory_ref": memory.as_ref()})
                )
            except (MemoryEntryNotFoundError, ArtifactNotFoundError):
                continue
            if entry.entry_id != citation.entry_id:
                continue
            if node.state == "inactive":
                memory = await service.forget(memory, entries=[entry], reason=f"lifecycle:{node.node_id}")
            else:
                memory = await service.reactivate(memory, entries=[entry], reason=f"lifecycle:{node.node_id}")


def _evaluate(
    nodes: list[LifecycleNodeRow],
    events: dict[str, LifecycleEventRow],
    /,
) -> dict[str, bool]:
    """Recompute availability for every node from committed events."""

    states: dict[str, bool] = {}
    for node in nodes:
        if node.kind == "source":
            last = events.get(node.node_id)
            states[node.node_id] = True if last is None else last.available
    pending = [node for node in nodes if node.kind != "source"]
    for _ in range(len(nodes) + 1):
        if not pending:
            break
        remaining: list[LifecycleNodeRow] = []
        progressed = False
        for node in pending:
            parent_states = [states.get(parent) for parent in node.parents]
            if any(state is None for state in parent_states):
                remaining.append(node)
                continue
            if node.policy == "all":
                available = all(parent_states)
            else:
                available = any(parent_states)
            states[node.node_id] = available
            progressed = True
        if not progressed:
            break
        pending = remaining
    for node in pending:
        states[node.node_id] = False
    return states


def _ancestors(node_id: str, by_id: dict[str, LifecycleNodeRow], /) -> set[str]:
    """Return every node reachable from node_id through parent edges."""

    seen: set[str] = set()
    stack = list(by_id[node_id].parents)
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        parent = by_id.get(current)
        if parent is not None:
            stack.extend(parent.parents)
    return seen


def _validate_node_id(node_id: str, /) -> None:
    if not isinstance(node_id, str) or not node_id or node_id.strip() != node_id:
        raise SourceLifecycleError("node_id must be a nonempty trimmed string")


async def _head_or_none(service: MemoryService, artifact_id: str, /) -> Memory | None:
    try:
        return await service.head(artifact_id)
    except ArtifactNotFoundError:
        return None


async def _cited_entry(
    service: MemoryService,
    memory: Memory,
    citation: MemoryCitation,
    /,
) -> MemoryEntryVersion:
    if not any(
        item.entry_id == citation.entry_id and item.entry_version_id == citation.entry_version_id
        for item in memory.content.manifest.entries
    ):
        raise MemoryEntryNotFoundError(citation.entry_id)
    return await service.validate_citation(citation)

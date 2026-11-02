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

"""Cross-call embedding request coalescing with a bounded LRU vector cache.

Concurrent ``embed`` calls share identical texts through in-flight batches:
one underlying provider request serves every waiter, late callers join
already-running requests, and successful vectors are cached for later calls.
Cancellation of one caller never cancels the shared underlying work, failed
batches are never cached, and each caller's usage aggregates only the
distinct underlying batches it actually waited on.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import TYPE_CHECKING

from powercontext.builtin.inference.models import EmbeddingResult, InferenceUsage

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory import EmbeddingProfile

__all__ = ["CoalescingEmbeddingModel"]

Vector: type = tuple[float, ...]


class _Batch:
    """One in-flight underlying request shared by any number of callers."""

    __slots__ = ("done", "error", "sealed", "sealed_event", "texts", "usage", "vectors")

    def __init__(self, text: str) -> None:
        self.texts: list[str] = [text]
        self.sealed = False
        self.sealed_event = asyncio.Event()
        self.done = asyncio.Event()
        self.vectors: tuple[Vector, ...] | None = None
        self.usage: InferenceUsage | None = None
        self.error: BaseException | None = None


class CoalescingEmbeddingModel:
    """Merge concurrent identical embedding texts and cache successful vectors."""

    def __init__(
        self,
        model: object,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if not hasattr(model, "profile") or not callable(getattr(model, "embed", None)):
            raise ValueError("model must expose a profile and an embed coroutine")
        if type(max_batch_size) is not int or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")
        if not isinstance(coalesce_ms, (int, float)) or isinstance(coalesce_ms, bool) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be a nonnegative number")
        if type(cache_size) is not int or cache_size < 0:
            raise ValueError("cache_size must be a nonnegative integer")
        self._model = model
        self.profile: EmbeddingProfile = model.profile
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = coalesce_ms / 1000
        self._cache_size = cache_size
        self._lock = asyncio.Lock()
        self._pending: dict[str, _Batch] = {}
        self._cache: OrderedDict[str, Vector] = OrderedDict()
        self._tasks: set[asyncio.Task[None]] = set()

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Return one ordered vector per input text, sharing concurrent work."""

        texts = tuple(texts)
        if not texts:
            return EmbeddingResult(vectors=())
        unique = tuple(dict.fromkeys(texts))
        async with self._lock:
            cached: dict[str, Vector] = {}
            missing: list[str] = []
            for text in unique:
                vector = self._cache.get(text)
                if vector is None:
                    missing.append(text)
                else:
                    self._cache.move_to_end(text)
                    cached[text] = vector
            plan: list[_Batch] = []
            current: _Batch | None = None
            for text in missing:
                batch = self._pending.get(text)
                if batch is not None:
                    # The text already belongs to an in-flight batch; join it as a waiter.
                    current = None
                    plan.append(batch)
                    continue
                if current is None or len(current.texts) >= self._max_batch_size:
                    current = _Batch(text)
                    self._pending[text] = current
                    self._spawn_batch(current)
                else:
                    current.texts.append(text)
                    self._pending[text] = current
                if len(current.texts) >= self._max_batch_size:
                    self._seal(current)
                plan.append(current)
        awaited: dict[int, _Batch] = {}
        for batch in plan:
            awaited[id(batch)] = batch
        for batch in awaited.values():
            await batch.done.wait()
        vectors_by_text = dict(cached)
        for batch in awaited.values():
            if batch.error is not None:
                raise batch.error
            for text, vector in zip(batch.texts, batch.vectors or (), strict=True):
                vectors_by_text[text] = vector
        vectors = tuple(vectors_by_text[text] for text in texts)
        return EmbeddingResult(vectors=vectors, usage=_sum_usage(batch.usage for batch in awaited.values()))

    def _spawn_batch(self, batch: _Batch, /) -> None:
        task = asyncio.create_task(self._run_batch(batch))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _seal(self, batch: _Batch, /) -> None:
        """Stop accepting new texts into the batch; waiters may still join."""

        batch.sealed = True
        batch.sealed_event.set()

    async def _run_batch(self, batch: _Batch, /) -> None:
        try:
            if self._coalesce_seconds > 0:
                try:
                    await asyncio.wait_for(batch.sealed_event.wait(), timeout=self._coalesce_seconds)
                except TimeoutError:
                    pass
            async with self._lock:
                self._seal(batch)
                texts = tuple(batch.texts)
            result = await self._model.embed(texts)
            vectors = tuple(tuple(vector) for vector in result.vectors)
            if len(vectors) != len(texts):
                raise ValueError("underlying embedding returned a vector count that does not match the batch texts")
            async with self._lock:
                if self._cache_size > 0:
                    for text, vector in zip(texts, vectors, strict=True):
                        self._cache[text] = vector
                        self._cache.move_to_end(text)
                        while len(self._cache) > self._cache_size:
                            self._cache.popitem(last=False)
                for text in texts:
                    if self._pending.get(text) is batch:
                        del self._pending[text]
                batch.vectors = vectors
                batch.usage = result.usage
        except BaseException as error:  # noqa: BLE001 - every waiter must observe the failure
            async with self._lock:
                for text in tuple(batch.texts):
                    if self._pending.get(text) is batch:
                        del self._pending[text]
                batch.error = error
        finally:
            batch.done.set()


def _sum_usage(usages, /) -> InferenceUsage:
    """Aggregate provider usage, keeping unset token fields unset."""

    items = [usage for usage in usages if usage is not None]
    inputs = [usage.input_tokens for usage in items if usage.input_tokens is not None]
    outputs = [usage.output_tokens for usage in items if usage.output_tokens is not None]
    return InferenceUsage(
        requests=sum(usage.requests for usage in items),
        input_tokens=sum(inputs) if inputs else None,
        output_tokens=sum(outputs) if outputs else None,
    )

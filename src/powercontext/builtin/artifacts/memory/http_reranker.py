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

"""HTTP scoring-service reranker with cross-batch stable selection."""

from __future__ import annotations

import json
import math
from typing import Protocol

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision, _validate_bounds
from powercontext.builtin.inference import InferenceUsage

DEFAULT_HTTP_RERANK_POLICY_ID = "http-cross-encoder-v1"
DEFAULT_HTTP_RERANK_BATCH_SIZE = 32


class _AsyncPostClient(Protocol):
    """The one httpx method this reranker needs from the caller-provided client."""

    async def post(self, url: str, json: object, /) -> httpx.Response:  # noqa: A002 - httpx parameter name
        ...


class HTTPMemoryReranker:
    """Score Memory candidates with an HTTP service and select across all batches.

    Candidates keep their one-based global rank while being sent in batches of at most
    ``batch_size``. Each batch response must cover exactly its own ranks once, and the
    final selection over the complete pool orders by score descending with ties broken
    by the original global rank ascending.
    """

    policy_id: str

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = DEFAULT_HTTP_RERANK_BATCH_SIZE,
        policy_id: str = DEFAULT_HTTP_RERANK_POLICY_ID,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be at least one")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Send every candidate once, then select the top ranks from all scores."""

        _validate_bounds(len(candidates), limit)
        scores: dict[int, float] = {}
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = candidates[start : start + self._batch_size]
            expected_ranks = range(start + 1, start + len(batch) + 1)
            response = await self._client.post(
                self._endpoint,
                json={
                    "query": query,
                    "candidates": [
                        {"rank": rank, "text": candidate.text}
                        for rank, candidate in zip(expected_ranks, batch, strict=True)
                    ],
                },
            )
            response.raise_for_status()
            requests += 1
            for rank, score in _parse_scores(response, expected_ranks=expected_ranks):
                scores[rank] = score
        ordered_ranks = sorted(scores, key=lambda rank: (-scores[rank], rank))
        return MemoryRerankDecision(
            selected_ranks=tuple(ordered_ranks[:limit]),
            usage=InferenceUsage(requests=requests),
        )


def _parse_scores(response: httpx.Response, /, *, expected_ranks: range) -> list[tuple[int, float]]:
    """Decode one batch response and require exact one-to-one rank coverage."""

    try:
        body = json.loads(response.content)
    except json.JSONDecodeError as error:
        raise ValueError("reranker response must be valid JSON") from error  # noqa: TRY003
    if not isinstance(body, dict) or set(body) != {"scores"}:
        raise ValueError("reranker response must contain exactly a scores field")  # noqa: TRY003
    score_records = body["scores"]
    if not isinstance(score_records, list):
        raise ValueError("reranker scores must be a list")  # noqa: TRY003
    parsed: list[tuple[int, float]] = []
    seen: set[int] = set()
    for record in score_records:
        rank, score = _parse_score_record(record)
        if rank not in expected_ranks:
            raise ValueError("reranker scores must cover exactly this batch's ranks")  # noqa: TRY003
        if rank in seen:
            raise ValueError("reranker scores must mention each rank once")  # noqa: TRY003
        seen.add(rank)
        parsed.append((rank, score))
    if seen != set(expected_ranks):
        raise ValueError("reranker scores must cover every rank of this batch")  # noqa: TRY003
    return parsed


def _parse_score_record(record: object, /) -> tuple[int, float]:
    if not isinstance(record, dict) or set(record) != {"rank", "score"}:
        raise ValueError("each score record must contain exactly rank and score")  # noqa: TRY003
    rank = record["rank"]
    if type(rank) is not int:
        raise ValueError("each score rank must be an integer")  # noqa: TRY003
    score = record["score"]
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
        raise ValueError("each score value must be a finite number")  # noqa: TRY003
    return rank, float(score)


__all__ = ["DEFAULT_HTTP_RERANK_BATCH_SIZE", "DEFAULT_HTTP_RERANK_POLICY_ID", "HTTPMemoryReranker"]

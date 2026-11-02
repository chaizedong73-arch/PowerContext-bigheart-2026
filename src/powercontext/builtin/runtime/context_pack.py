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

"""Context Pack wire protocol for incremental context transport.

A pack carries one prepared context as UTF-8-valid byte chunks. Its digest covers
only the canonical ``schema``/``items`` JSON so receivers can verify content without
the self-referential ``byte_count`` field, which is instead checked against the
serialized length. Deltas name the base pack they extend, keep a common item prefix
and carry the target-only suffix, so any receiver holding the base pack rebuilds the
target pack from serialized bytes alone.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass

_PACK_SCHEMA = "powercontext.context-pack.v1"
_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
_DIGEST_PREFIX = "sha256:"
_PACK_FIELDS = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})
_DEFAULT_CHUNK_BYTES = 512


class ContextPackError(ValueError):
    """Raised for protocol violations or chunking failures."""


def _canonical_json(payload: dict[str, object], /) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _encode_items(items: tuple[bytes, ...], /) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decode_items(value: object, /) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError("items must be a list of base64 strings")  # noqa: TRY003
    try:
        return tuple(base64.b64decode(item, validate=True) for item in value)
    except (binascii.Error, TypeError, ValueError) as error:
        raise ContextPackError("items must be standard base64 strings") from error  # noqa: TRY003


def _validate_digest(value: object, /) -> str:
    if not isinstance(value, str) or not value.startswith(_DIGEST_PREFIX):
        raise ContextPackError("digest must be a sha256:<hex> string")  # noqa: TRY003
    hex_part = value[len(_DIGEST_PREFIX) :]
    if len(hex_part) != hashlib.sha256().digest_size * 2:
        raise ContextPackError("digest must be a sha256:<hex> string")  # noqa: TRY003
    try:
        binascii.unhexlify(hex_part)
    except binascii.Error as error:
        raise ContextPackError("digest must be a sha256:<hex> string") from error  # noqa: TRY003
    return value


def _content_digest(items: tuple[bytes, ...], /) -> str:
    canonical = _canonical_json({"schema": _PACK_SCHEMA, "items": _encode_items(items)})
    return _DIGEST_PREFIX + hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class ContextPack:
    """One complete context as byte chunks plus digest and exact wire size."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: tuple[bytes, ...], /) -> ContextPack:
        items = tuple(items)
        payload: dict[str, object] = {
            "schema": _PACK_SCHEMA,
            "items": _encode_items(items),
            "digest": _content_digest(items),
        }
        byte_count = 0
        while True:
            encoded = _canonical_json({**payload, "byte_count": byte_count})
            if len(encoded) == byte_count:
                return cls(items=items, digest=str(payload["digest"]), byte_count=byte_count)
            byte_count = len(encoded)

    @classmethod
    def from_content(cls, content: str, /, *, chunk_bytes: int = _DEFAULT_CHUNK_BYTES) -> ContextPack:
        """Split UTF-8 content into deterministic chunks that never split a character."""

        if chunk_bytes < 1:
            raise ContextPackError("chunk_bytes must be positive")  # noqa: TRY003
        encoded = content.encode("utf-8")
        items: list[bytes] = []
        start = 0
        while start < len(encoded):
            end = min(start + chunk_bytes, len(encoded))
            while end < len(encoded) and (encoded[end] & 0b1100_0000) == 0b1000_0000:
                end -= 1
            if end == start:
                raise ContextPackError("chunk_bytes cannot hold a single UTF-8 character")  # noqa: TRY003
            items.append(encoded[start:end])
            start = end
        return cls.build(tuple(items))

    def to_bytes(self, /) -> bytes:
        return _canonical_json({
            "schema": _PACK_SCHEMA,
            "items": _encode_items(self.items),
            "digest": self.digest,
            "byte_count": self.byte_count,
        })

    @classmethod
    def from_bytes(cls, data: bytes, /) -> ContextPack:
        try:
            payload = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ContextPackError("pack must be canonical JSON") from error  # noqa: TRY003
        if not isinstance(payload, dict) or set(payload) != _PACK_FIELDS:
            raise ContextPackError("pack must contain exactly schema, items, digest and byte_count")  # noqa: TRY003
        if payload["schema"] != _PACK_SCHEMA:
            raise ContextPackError("unknown pack schema")  # noqa: TRY003
        items = _decode_items(payload["items"])
        digest = _validate_digest(payload["digest"])
        if digest != _content_digest(items):
            raise ContextPackError("pack digest mismatch")  # noqa: TRY003
        byte_count = payload["byte_count"]
        if type(byte_count) is not int or byte_count != len(data):
            raise ContextPackError("pack byte_count mismatch")  # noqa: TRY003
        return cls(items=items, digest=digest, byte_count=byte_count)


@dataclass(frozen=True)
class ContextPackDelta:
    """Reuse instructions from one base pack toward one target pack."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]
    byte_count: int = 0

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack, /) -> ContextPackDelta:
        prefix = 0
        for base_item, target_item in zip(base.items, target.items, strict=False):
            if base_item != target_item:
                break
            prefix += 1
        appended = target.items[prefix:]
        encoded = _canonical_json({
            "schema": _DELTA_SCHEMA,
            "base_digest": base.digest,
            "target_digest": target.digest,
            "prefix_items": prefix,
            "appended": _encode_items(appended),
        })
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix,
            appended=appended,
            byte_count=len(encoded),
        )

    def to_bytes(self, /) -> bytes:
        return _canonical_json({
            "schema": _DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": _encode_items(self.appended),
        })

    @classmethod
    def from_bytes(cls, data: bytes, /) -> ContextPackDelta:
        try:
            payload = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ContextPackError("delta must be canonical JSON") from error  # noqa: TRY003
        if not isinstance(payload, dict) or set(payload) != _DELTA_FIELDS:
            raise ContextPackError(  # noqa: TRY003
                "delta must contain exactly schema, base_digest, target_digest, prefix_items and appended"
            )
        if payload["schema"] != _DELTA_SCHEMA:
            raise ContextPackError("unknown delta schema")  # noqa: TRY003
        base_digest = _validate_digest(payload["base_digest"])
        target_digest = _validate_digest(payload["target_digest"])
        prefix_items = payload["prefix_items"]
        if type(prefix_items) is not int or prefix_items < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")  # noqa: TRY003
        return cls(
            base_digest=base_digest,
            target_digest=target_digest,
            prefix_items=prefix_items,
            appended=_decode_items(payload["appended"]),
            byte_count=len(data),
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta, /) -> ContextPack:
    """Rebuild the target pack from a serialized base pack and delta, or fail."""

    if delta.base_digest != base.digest:
        raise ContextPackError("delta base digest mismatch")  # noqa: TRY003
    if not 0 <= delta.prefix_items <= len(base.items):
        raise ContextPackError("delta prefix exceeds the base pack")  # noqa: TRY003
    target = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if target.digest != delta.target_digest:
        raise ContextPackError("delta target digest mismatch")  # noqa: TRY003
    return target


__all__ = ["ContextPack", "ContextPackDelta", "ContextPackError", "apply_context_pack_delta"]

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

"""Row-level CSV capture with exact record identity and text-evidence projection."""

from __future__ import annotations
import csv
import io
import json
from typing import ClassVar
from urllib.parse import quote

from pydantic import BaseModel

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

CSV_ROW_SOURCE_NAME = "csv-row"
_CSV_IDENTITY_PREFIX = "csv:"
_UTF8_BOM = "\ufeff"


class CSVDocumentInput(BaseModel):
    """Complete offline byte content of one CSV document."""

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One exactly captured CSV data row with its identity inputs."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    """Persisted evidence for one CSV row keyed by document and primary key."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def parse_csv_document(value: CSVDocumentInput, /) -> tuple[CSVRowInput, ...]:
    """Parse and validate the whole document first, then return every data row.

    All encoding, CSV format, and record validation failures raise ``ValueError``
    without returning partial rows.
    """

    _require_document_id(value.document_id)
    try:
        text = value.content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("document content must be valid UTF-8") from error  # noqa: TRY003
    # The BOM is only accepted at the very beginning and never appears in column names.
    if text.startswith(_UTF8_BOM):
        text = text[1:]

    # newline="" keeps \r\n inside quoted fields byte-exact for field fidelity.
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        records = list(reader)
    except csv.Error as error:
        raise ValueError(f"malformed CSV input: {error}") from error  # noqa: TRY003

    if not records:
        raise ValueError("document must contain a header record")  # noqa: TRY003
    columns, key_index = _validated_header(records[0], value.primary_key)
    return _validated_rows(records[1:], value, columns, key_index)


def _validated_header(header: list[str], primary_key: str, /) -> tuple[tuple[str, ...], int]:
    if not header or any(column == "" or not column.strip() for column in header):
        raise ValueError("header fields must be non-empty and not pure whitespace")  # noqa: TRY003
    if len(set(header)) != len(header):
        raise ValueError("header column names must be unique")  # noqa: TRY003
    if primary_key not in header:
        raise ValueError("primary_key must match one header column exactly")  # noqa: TRY003
    return tuple(header), header.index(primary_key)


def _validated_rows(
    records: list[list[str]],
    value: CSVDocumentInput,
    columns: tuple[str, ...],
    key_index: int,
    /,
) -> tuple[CSVRowInput, ...]:
    seen_keys: set[str] = set()
    rows: list[CSVRowInput] = []
    for record in records:
        # Only completely empty physical records after the header are ignored.
        if not record:
            continue
        if len(record) != len(columns):
            raise ValueError("data record width must equal the header width")  # noqa: TRY003
        key_value = record[key_index]
        _require_key_value(key_value)
        if key_value in seen_keys:
            raise ValueError("primary key values must be unique within one document")  # noqa: TRY003
        seen_keys.add(key_value)
        rows.append(
            CSVRowInput(
                document_id=value.document_id,
                primary_key=value.primary_key,
                primary_key_value=key_value,
                columns=columns,
                values=tuple(record),
            )
        )
    return tuple(rows)


def csv_row_source_id(document_id: str, primary_key_value: str, /) -> str:
    """Build the stable row identity with collision-free percent encoding."""

    _require_document_id(document_id)
    _require_key_value(primary_key_value)
    identity = f"{_CSV_IDENTITY_PREFIX}{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"source identity must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return identity


def _require_document_id(document_id: str, /) -> None:
    if not document_id:
        raise ValueError("document_id must be non-empty")  # noqa: TRY003
    if document_id != document_id.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")  # noqa: TRY003


def _require_key_value(key_value: str, /) -> None:
    if key_value == "" or not key_value.strip():
        raise ValueError("primary key value must not be empty or pure whitespace")  # noqa: TRY003


class CSVRowSourceAdapter:
    """Resolve and read the row-level CSV Source."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class CSVRowTextEvidenceProjection:
    """Project one row as canonical text evidence without rewriting any value."""

    name: ClassVar[str] = TEXT_EVIDENCE_PROJECTION_KEY.name
    version: ClassVar[str] = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: ClassVar[type[BaseModel]] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


CSV_ROW_SOURCE_ADAPTER = CSVRowSourceAdapter()
CSV_ROW_SOURCE_DEFINITION = AdapterSourceDefinition(
    CSV_ROW_SOURCE_ADAPTER,
    projections=(CSVRowTextEvidenceProjection(),),
)

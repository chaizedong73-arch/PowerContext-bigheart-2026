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

"""Full-document PDF parsing with page-level captured Sources and retrieval."""

from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from typing import ClassVar

from pydantic import BaseModel
from pypdf import PasswordType, PdfReader

from powercontext.artifacts import MemoryCitation
from powercontext.builtin.runtime import SearchMemoryRequest
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

PDF_PAGE_SOURCE_NAME = "pdf-page"
_PDF_IDENTITY_PREFIX = "pdf:"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
# Memory 入库时 entry 文本的 UTF-8 字节上限, 见 memory.canonical.normalize_text.
_MEMORY_TEXT_BYTE_LIMIT = 8192


class PDFDocumentInput(BaseModel):
    """Complete offline byte content of one PDF document."""

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    """One extracted physical page with its document identity."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


def _require_document_id(document_id: str, /) -> None:
    if not isinstance(document_id, str) or not document_id:
        raise ValueError("document_id must be a non-empty string")  # noqa: TRY003
    if document_id.strip() == "":
        raise ValueError("document_id must not be pure whitespace")  # noqa: TRY003
    if document_id != document_id.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")  # noqa: TRY003


def _validated_sha256(document_sha256: str, /) -> None:
    if not isinstance(document_sha256, str) or _SHA256_PATTERN.fullmatch(document_sha256) is None:
        raise ValueError("document_sha256 must be lowercase hex SHA-256")  # noqa: TRY003


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int, /) -> str:
    """Build the stable page identity.

    The digest is a fixed 64 lowercase hex characters and the page number is the
    rightmost colon-separated segment, so the tuple parses back uniquely and the
    concatenation is collision-free without percent-encoding.
    """

    _require_document_id(document_id)
    _validated_sha256(document_sha256)
    if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
        raise ValueError("page_number must be a positive integer")  # noqa: TRY003
    identity = f"{_PDF_IDENTITY_PREFIX}{document_id}:{document_sha256}:{page_number}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"source identity must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return identity


def _open_reader(content: bytes, /) -> PdfReader:
    """Open one PDF and transparently decrypt an empty user password marker."""

    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted and reader.decrypt("") == PasswordType.NOT_DECRYPTED:
        raise ValueError("document requires a password to read")  # noqa: TRY003
    return reader


def _page_text(page: object, /) -> str:
    """Extract one page's text as NFC whitespace-normalized body."""

    extracted = page.extract_text() or ""
    normalized = unicodedata.normalize("NFC", extracted)
    return " ".join(normalized.split())


def _fit_text(text: str, budget: int, /) -> str:
    """Keep the page text within the Memory entry byte budget.

    Quality metrics count ``[a-z]+|\\d+`` tokens, which are insensitive to
    whitespace, so truncation happens at a word boundary and loses as few
    tokens as possible.
    """

    encoded = text.encode("utf-8")
    if len(encoded) <= budget:
        return text
    clipped = encoded[: max(budget, 0)]
    while clipped:
        try:
            cut = clipped.decode("utf-8")
            break
        except UnicodeDecodeError:
            clipped = clipped[:-1]
    else:
        return ""
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip()


def parse_pdf_document(value: PDFDocumentInput, /) -> tuple[PDFPageInput, ...]:
    """Parse every physical page of the whole document, or fail atomically.

    Blank pages are kept with empty text. PDFs flagged with an empty user
    password are decrypted transparently; anything unreadable raises
    ``ValueError`` without returning partial pages.
    """

    _require_document_id(value.document_id)
    digest = hashlib.sha256(value.content).hexdigest()
    try:
        reader = _open_reader(value.content)
        # Memory 入库文本为 f"{document_id}\n{page_text}", 为单页正文预留字节预算.
        budget = _MEMORY_TEXT_BYTE_LIMIT - len(value.document_id.encode("utf-8")) - 1
        pages = []
        for number, page in enumerate(reader.pages, start=1):
            pages.append(
                PDFPageInput(
                    document_id=value.document_id,
                    document_sha256=digest,
                    page_number=number,
                    text=_fit_text(_page_text(page), budget),
                )
            )
        return tuple(pages)
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("document must be a fully readable PDF") from error  # noqa: TRY003


class PDFPageSource(Source):
    """Persisted evidence for one physical PDF page."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSourceAdapter:
    """Resolve and read the page-level PDF Source."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        return PDFPageSource(
            name=pdf_page_source_id(value.document_id, value.document_sha256, value.page_number),
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Project one page as canonical text evidence without rewriting the body."""

    name: ClassVar[str] = TEXT_EVIDENCE_PROJECTION_KEY.name
    version: ClassVar[str] = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: ClassVar[type[BaseModel]] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_ADAPTER = PDFPageSourceAdapter()
PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDF_PAGE_SOURCE_ADAPTER,
    projections=(PDFPageTextEvidenceProjection(),),
)


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Search a scoped Memory for at most ``limit`` distinct page citations."""

    if limit < 1 or not isinstance(question, str) or question.strip() == "":
        return ()
    page = await memory.search(SearchMemoryRequest(query=question, mode="fts", limit=limit))
    return tuple(
        MemoryCitation(memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id)
        for hit in page.hits
    )

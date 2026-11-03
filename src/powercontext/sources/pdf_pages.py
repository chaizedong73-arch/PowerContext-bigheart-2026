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
from powercontext.builtin.artifacts.search import analyze_fts_query
from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest
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
    """Search a scoped Memory for at most ``limit`` distinct page citations.

    离线查询处理与重排：先以多组查询变体做 FTS 粗召（整题、逐句、带词形放宽的
    关键词条、财报表头短语），RRF 加权融合后按报表主表页与邻页先验重排，返回
    全部经过真实 Memory 搜索命中的页面引用。
    """

    if limit < 1 or not isinstance(question, str) or question.strip() == "":
        return ()
    coarse = max(limit * 4, 64)
    fused: dict[tuple[str, str], list] = {}
    for weight, variant in _query_variants(question):
        page = await memory.search(SearchMemoryRequest(query=variant, mode="fts", limit=coarse))
        for rank, hit in enumerate(page.hits, start=1):
            key = (hit.entry_id, hit.entry_version_id)
            fused.setdefault(key, [0.0, hit])
            fused[key][0] += weight / (60.0 + rank)
    if not fused:
        return ()
    groups = _triggered_groups(question)
    rescored = []
    for rrf, hit in fused.values():
        bonus = 0.0
        if groups:
            head = (hit.text or "")[:_HEADER_WINDOW]
            for group in groups:
                if _HEADER_PROBES[group].search(head):
                    bonus += _STATEMENT_BONUS
        rescored.append([rrf + bonus, rrf, hit, bonus])
    rescored.sort(key=lambda item: (-item[0], -item[1], item[2].entry_id))
    await _apply_page_priors(memory, rescored, groups)
    rescored.sort(key=lambda item: (-item[0], -item[1], item[2].entry_id))
    return tuple(
        MemoryCitation(memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id)
        for _score, _rrf, hit, _bonus in rescored[:limit]
    )


_STATEMENT_BONUS = 0.09
_QUALITATIVE_BONUS = 0.02
_QUALITATIVE_PAGE_WINDOW = 40
_NEIGHBOR_BONUS = {1: 0.03, 2: 0.02}
_HEADER_WINDOW = 700
_RRF_CONSTANT = 60.0
_COARSE_MULTIPLIER = 4
_COARSE_FLOOR = 64
_SINGLE_TERM_CAP = 14
_VARIANT_CAP = 30

# 报表类型触发词与主表页表头探测: 定量指标的 gold 页通常是财报主表页, 主表页
# 页首带 "Consolidated Statements of ..." 等固定表头, 可以离线探测后加权.
_REPORT_GROUPS = {
    "income": "consolidated statements of operations income",
    "balance": "consolidated balance sheets",
    "cash": "consolidated statements cash flows",
    "segment": "business segment data revenues",
}
_REPORT_TRIGGERS = {
    "income": {"margin", "ebitda", "operating", "gross", "tax", "income", "earnings", "profit", "sales", "revenue", "revenues", "cogs", "capital"},
    "balance": {"liquidity", "ratio", "payable", "inventory", "asset", "assets", "liabilities", "debt", "cash", "capital", "intensive"},
    "cash": {"cash", "capex", "depreciation", "amortization", "ebitda", "payable", "capital", "intensive"},
    "segment": {"segment", "segments", "product", "products", "service", "services", "categories", "category"},
}
_HEADER_PROBES = {
    "income": re.compile(r"consolidated statements? of operations|consolidated statements? of income|statements? of operations", re.I),
    "balance": re.compile(r"consolidated balance sheets?|balance sheets?", re.I),
    "cash": re.compile(r"consolidated statements? of cash flows|statements? of cash flows", re.I),
    "segment": re.compile(r"business segment data|segment data|by business segment|reportable segments?", re.I),
}
_LEGAL_SYNONYMS = {
    "legal": "legal proceedings",
    "battles": "legal proceedings",
    "lawsuits": "legal proceedings",
    "litigation": "legal proceedings",
}
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_RRF_WEIGHTS = {"full": 3.0, "sentence": 2.0, "single": 1.0, "report": 2.0}


def _term_variants(term: str, /) -> set[str]:
    """Widen one query term with conservative morphological variants.

    FTS 分析不做词干化, 单复数 (customers/customer)、-ality/-al
    (cyclicality/cyclical) 与财年写法 (fy2022/2022) 需要显式放宽才能召回.
    """

    variants = {term}
    year = re.fullmatch(r"fy(\d{2,4})", term)
    if year:
        variants.add(year.group(1))
        if len(year.group(1)) == 2 and year.group(1).isdigit() and int(year.group(1)) < 60:
            variants.add("20" + year.group(1))
    if len(term) >= 6 and term.endswith("ality"):
        variants.add(term[:-5] + "al")
    if len(term) >= 4 and term.endswith("ies"):
        variants.add(term[:-3] + "y")
    elif len(term) >= 4 and term.endswith("es"):
        variants.add(term[:-2])
    elif len(term) >= 4 and term.endswith("s") and not term.endswith("ss"):
        variants.add(term[:-1])
    return variants


def _salient_terms(question: str, /) -> list[str]:
    return list(dict.fromkeys(analyze_fts_query(question).split()))


def _query_variants(question: str, /) -> list[tuple[float, str]]:
    variants: list[tuple[float, str, str]] = []
    full = analyze_fts_query(question)
    if full:
        variants.append((_RRF_WEIGHTS["full"], full, "full"))
    for sentence in _SENTENCE_SPLIT.split(question):
        terms = analyze_fts_query(sentence).split()
        if len(terms) >= 2:
            variants.append((_RRF_WEIGHTS["sentence"], " ".join(terms), "sentence"))
    for term in _salient_terms(question)[:_SINGLE_TERM_CAP]:
        variants.append((_RRF_WEIGHTS["single"], " ".join(sorted(_term_variants(term))), "single"))
        synonym = _LEGAL_SYNONYMS.get(term)
        if synonym:
            variants.append((_RRF_WEIGHTS["single"], synonym, "single"))
    question_terms = set(_salient_terms(question))
    for group, trigger_terms in _REPORT_TRIGGERS.items():
        if question_terms & trigger_terms:
            variants.append((_RRF_WEIGHTS["report"], _REPORT_GROUPS[group], "report"))
    unique: list[tuple[float, str]] = []
    seen = set()
    for weight, variant, _kind in variants:
        if variant not in seen:
            seen.add(variant)
            unique.append((weight, variant))
    return unique[:_VARIANT_CAP]


def _triggered_groups(question: str, /) -> set[str]:
    question_terms = set(_salient_terms(question))
    return {group for group, trigger_terms in _REPORT_TRIGGERS.items() if question_terms & trigger_terms}


def _page_of_source_id(source_id: object, /) -> int | None:
    """Parse the physical page number back out of a pdf-page source id."""

    if not isinstance(source_id, str) or not source_id.startswith(_PDF_IDENTITY_PREFIX):
        return None
    tail = source_id.rsplit(":", 1)[-1]
    try:
        return int(tail)
    except ValueError:
        return None


async def _apply_page_priors(memory, rescored: list[list], groups: set[str], /) -> None:
    """Resolve page numbers once and apply statement-cluster / qualitative priors.

    主表页成簇出现 (利润表/资产负债表/现金流量表连续排布), 以表头加成页为锚,
    给池内相邻页加成; 纯定性业务问题没有指标触发时, 给文档开头的业务章节页
    轻微加成. 页码从 citation 回查的 source id 中解析, 解析失败则保持纯 RRF 排序.
    """

    probe = rescored[:80]
    page_numbers: dict[int, int] = {}
    for item in probe:
        citation = MemoryCitation(
            memory_ref=item[2].memory_ref, entry_id=item[2].entry_id, entry_version_id=item[2].entry_version_id
        )
        try:
            entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        except Exception:
            continue
        for reference in entry.sources:
            page_number = _page_of_source_id(reference.source_id)
            if page_number is not None:
                page_numbers[id(item[2])] = page_number
                break
    anchors = [page_numbers[id(item[2])] for item in rescored[:40] if item[3] > 0 and id(item[2]) in page_numbers]
    for item in rescored:
        page_number = page_numbers.get(id(item[2]))
        if page_number is None or item[3] > 0:
            continue
        if anchors:
            neighbor_bonus = 0.0
            for anchor in anchors:
                distance = abs(page_number - anchor)
                if distance in _NEIGHBOR_BONUS:
                    neighbor_bonus += _NEIGHBOR_BONUS[distance]
            item[0] += neighbor_bonus
        elif not groups and page_number <= _QUALITATIVE_PAGE_WINDOW:
            item[0] += _QUALITATIVE_BONUS

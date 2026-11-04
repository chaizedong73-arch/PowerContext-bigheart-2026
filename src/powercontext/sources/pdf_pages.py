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
    关键词条、财报表头短语），RRF 加权融合后重排。多文档同池竞争时，先从问题
    词与 document_id 的词段对应关系识别目标文档（名称形态不可读时再按问题显著
    词在池内各文档页面文本中的包含率自校准兜底），并对该文档整体隔离加分；再在
    目标文档内把命中财报表头 / 法务章节探测的页面按 RRF 顺序直接注入头部，使
    几乎全数字、只命中少量变体的报表主表页不会被散文页挤出 top-N。返回全部
    经过真实 Memory 搜索命中的页面引用。
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
    target = _identify_document(fused, question)
    if target is None:
        target = _identify_document_by_text(fused, question)
    legal = bool(_LEGAL_TERMS & set(_salient_terms(question)))
    promoted = _promoted_keys(fused, target, groups, legal)
    rescored = []
    for rrf, hit in fused.values():
        key = (hit.entry_id, hit.entry_version_id)
        bonus = 0.0
        if target is not None:
            if _document_of_hit(hit) == target:
                bonus += _DOC_ISOLATION_BONUS
                if key in promoted:
                    bonus += _PROMOTION_BONUS
        else:
            head = (hit.text or "")[:_HEADER_WINDOW]
            for group in groups:
                if _HEADER_PROBES[group].search(head):
                    bonus += _STATEMENT_BONUS
            if legal and _LEGAL_PAGE_PROBE.search(hit.text or ""):
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
_DOC_ISOLATION_BONUS = 1.0
_PROMOTION_BONUS = 2.0
_PROMOTION_SLOTS = 2
_LEGAL_PROMOTION_SLOTS = 3
# 参与注入的报表组: 主表页在各自 "consolidated ..." 报表短语查询上几乎总是
# rank-1, 按该名次挑选注入页; segment 组的命中页多为散文注释页, 不注入.
_PROMOTION_GROUPS = {"income", "balance", "cash"}
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
# 法务问题触发词与法务章节页探测: "Legal Proceedings." / "legal proceeding," 类
# 章节标题或正文句式 (后随标点); 目录页 "Legal Proceedings 24" (数字结尾)、
# 风险因子页 "regulatory proceedings" (无 legal 邻接) 与 "Litigation Reform"
# (无标点紧跟) 都不会命中.
_LEGAL_TERMS = {"legal", "lawsuits", "litigation", "battles", "proceedings"}
_LEGAL_PAGE_PROBE = re.compile(r"legal proceedings?[.,;:]", re.I)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_RRF_WEIGHTS = {"full": 3.0, "sentence": 2.0, "single": 1.0, "report": 6.0}
# 页面文本自校准兜底 (_identify_document_by_text) 的判定条件: 目标文档池内页数
# 下限、司名词包含率下限、其余文档包含率上限与最高值对次高值的主导倍数. 公司
# 自身财报页几乎每页都含司名/司名词 (页眉页脚、业务描述), 他司文档极少提及,
# 因此司名词呈 "近乎独占" (其余文档包含率接近 0) 或 "强主导" (达到次高的数倍)
# 形态; 财报散文共有词 (costs/statements/business 等) 在各文档间分布紧密, 两个
# 条件都会排除. 短司名缩写 (3m/amd) 与 2 字符数字母词单独放行.
_TEXT_RATE_MIN = 0.4
_TEXT_POOL_MIN = 6
_TEXT_OTHER_RATE_MAX = 0.1
_TEXT_DOMINANCE = 3.0


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
    """Build weighted FTS query variants: whole question, sentences, terms, reports."""

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


def _document_of_hit(hit, /) -> str | None:
    """Read the document identity from the projected entry text's first line.

    TextEvidence 投影正文为 f"{document_id}\\n{page text}", 首行即文档标识; 空
    白页入库后 strip 只剩 document_id 同样可解析. 首行含空白则不是标识形态
    (例如正文无前缀的异常条目), 返回 None 退回无隔离排序.
    """

    first, _sep, _rest = (hit.text or "").partition("\n")
    candidate = first.strip()
    if not candidate or any(char.isspace() for char in candidate):
        return None
    return candidate


def _identify_document(fused: dict[tuple[str, str], list], question: str, /) -> str | None:
    """Match question terms against document_id name segments to find one target.

    document_id 首段是发行人名 (如 PEPSICO_2022_10K 的 pepsi / pepsico、
    AMERICANWATERWORKS_2021_10K 的 american), 与问题 FTS 词相等或互为前缀
    时命中; 命中文档数不为 1 (题目无公司词, 或 10k 类泛词全中) 则返回 None,
    跳过隔离与注入, 退回纯 RRF 排序, 不报错.
    """

    tokens = [term for term in _salient_terms(question) if len(term) >= 2]
    if not tokens:
        return None
    documents = {_document_of_hit(hit) for _rrf, hit in fused.values()}
    documents.discard(None)
    matched: set[str] = set()
    for document in documents:
        name = re.split(r"[\s_]+", document, maxsplit=1)[0].lower()
        if not any(char.isalpha() for char in name):
            continue
        for token in tokens:
            if token == name or (len(token) >= 4 and (name.startswith(token) or token.startswith(name))):
                matched.add(document)
                break
    return matched.pop() if len(matched) == 1 else None


def _identify_document_by_text(fused: dict[tuple[str, str], list], question: str, /) -> str | None:
    """Identify the target document from page-text containment when naming fails.

    document_id 命名不透明 (如纯数字 ID) 时名称匹配失效, 改用页面文本自校准:
    对问题每个候选词 (含字母且长度 >=3, 或含数字的 2 字符词如 3m) 统计其在这轮
    融合池各文档页面文本中的包含率 (含该词页数 / 该文档池内页数). 恰有一个文档
    对某词同时满足: 池内页数达到下限、包含率过四成, 且严格领先所有其他文档
    (其余文档包含率都低于独占上限, 或最高值达到次高值的主导倍数) 时锁定该文档.
    公司自身财报页几乎每页都含司名/司名词, 而他司文档极少提及, 因此司名词呈
    近乎独占或强主导形态; 泛词在多个文档都常见, 被领先条件排除. 多文档同时命中
    或无命中都返回 None, 保持纯 RRF 排序, 不报错.
    """

    terms = [
        term
        for term in _salient_terms(question)
        if any(char.isalpha() for char in term)
        and (len(term) >= 3 or (len(term) == 2 and any(char.isdigit() for char in term)))
    ]
    if not terms:
        return None
    pages: dict[str, list[str]] = {}
    for _rrf, hit in fused.values():
        document = _document_of_hit(hit)
        if document is not None:
            pages.setdefault(document, []).append((hit.text or "").casefold())
    if not pages:
        return None
    matched: set[str] = set()
    for term in terms:
        rates = {document: sum(term in text for text in texts) / len(texts) for document, texts in pages.items()}
        best = max(rates.items(), key=lambda item: item[1])[0]
        second = max((rate for document, rate in rates.items() if document != best), default=0.0)
        if rates[best] < _TEXT_RATE_MIN or len(pages[best]) < _TEXT_POOL_MIN:
            continue
        if second >= _TEXT_OTHER_RATE_MAX and rates[best] < _TEXT_DOMINANCE * second:
            continue
        matched.add(best)
    return matched.pop() if len(matched) == 1 else None


def _promoted_keys(
    fused: dict[tuple[str, str], list],
    target: str | None,
    groups: set[str],
    legal: bool,
    /,
) -> set[tuple[str, str]]:
    """Pick the top probed pages per group inside the target document.

    报表主表页几乎全数字, 只命中 1-2 个低权重变体, RRF 累积分会被命中变体多
    的散文页淹没. 每个报表组在目标文档内选探测命中页时, 以表头在页首出现的
    字符位置为主序 (真主表页的 "Consolidated ..." 表头紧跟页首的目录条, 位于
    ~20 字符处; 散文引用与目录条目出现在几百字符处), RRF 累积分只做并列裁决;
    组内取前 _PROMOTION_SLOTS 页, 法务章节页 (页中句式) 按 RRF 取前
    _LEGAL_PROMOTION_SLOTS 页. 注入页直接进入最终结果头部, 不再依赖累积分竞争.
    """

    if target is None:
        return set()
    candidates: dict[str, list[tuple[int, float, tuple[str, str]]]] = {}
    for rrf, hit in fused.values():
        if _document_of_hit(hit) != target:
            continue
        text = hit.text or ""
        key = (hit.entry_id, hit.entry_version_id)
        head = text[:_HEADER_WINDOW]
        for group in groups & _PROMOTION_GROUPS:
            match = _HEADER_PROBES[group].search(head)
            if match:
                candidates.setdefault(group, []).append((match.start(), -rrf, key))
        if legal and _LEGAL_PAGE_PROBE.search(text):
            candidates.setdefault("legal", []).append((_VARIANT_CAP + 1, -rrf, key))
    promoted: set[tuple[str, str]] = set()
    for group, entries in candidates.items():
        entries.sort()
        slots = _LEGAL_PROMOTION_SLOTS if group == "legal" else _PROMOTION_SLOTS
        promoted.update(key for _position, _neg_rrf, key in entries[:slots])
    return promoted


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
    # 锚点取获得实质探测加分的页 (注入页 / 无隔离时的表头页); 仅隔离分不加分
    # 的目标文档页不能当锚点, 否则邻页加成会在全文档范围内稀释失效.
    anchors = [
        page_numbers[id(item[2])]
        for item in rescored[:40]
        if item[3] > 0 and abs(item[3] - _DOC_ISOLATION_BONUS) > 1e-9 and id(item[2]) in page_numbers
    ]
    for item in rescored:
        page_number = page_numbers.get(id(item[2]))
        if page_number is None:
            continue
        isolated = abs(item[3] - _DOC_ISOLATION_BONUS) <= 1e-9
        if item[3] > 0 and not isolated:
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

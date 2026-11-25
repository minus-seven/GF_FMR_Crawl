#!/usr/bin/env python
"""Local-first deterministic extraction for templated factsheet fields.

This module is intentionally independent from any paid model. It builds a
page/block evidence index from MinerU Markdown and ``content_list.json``, then
extracts scalar fields with label/value pairing, unit/currency normalization,
cross-source conflict checks and explicit disclosure states.

The module is a reference implementation for the local-first pipeline. It does
not replace the production extractor; callers can opt in field by field.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

try:
    from structured_data.rating import RATING_RANK, normalize_rating
except ImportError:  # pragma: no cover - standalone invocation
    from rating import RATING_RANK, normalize_rating  # type: ignore


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

STATUS_OK = "ok"
STATUS_ABSENT = "absent"
STATUS_UNPARSED = "unparsed"
STATUS_CONFLICT = "conflict"


@dataclass(frozen=True)
class EvidenceBlock:
    """One addressable unit of source evidence.

    ``page`` is zero-based; ``index`` is unique within the document. ``rows`` is
    populated for HTML tables so downstream parsers do not need to re-parse the
    markup.
    """

    page: int
    index: int
    kind: str
    text: str
    bbox: tuple[float, float, float, float] | None = None
    source: str = "mineru"
    rows: tuple[tuple[str, ...], ...] = ()

    def ref(self, quote: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "page": self.page,
            "block": self.index,
            "kind": self.kind,
            "source": self.source,
        }
        if self.bbox:
            payload["bbox"] = list(self.bbox)
        if quote:
            payload["quote"] = quote[:240]
        return payload


@dataclass
class Candidate:
    """A field candidate with provenance and routing metadata."""

    field: str
    value: Any = None
    status: str = STATUS_ABSENT
    rule: str = ""
    confidence: float = 0.0
    evidence: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "status": self.status,
            "rule": self.rule,
            "confidence": round(self.confidence, 4),
            "evidence": self.evidence,
            "meta": self.meta,
        }


@dataclass
class LocalExtraction:
    """Result of deterministic scalar extraction."""

    fields: dict[str, Candidate]
    routing: dict[str, list[str]]
    blocks: list[EvidenceBlock]
    disclosures: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self, *, include_blocks: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "fields": {name: item.to_dict() for name, item in self.fields.items()},
            "routing": self.routing,
            "disclosures": self.disclosures,
        }
        if include_blocks:
            payload["blocks"] = [
                {
                    "page": block.page,
                    "index": block.index,
                    "kind": block.kind,
                    "source": block.source,
                    "text": block.text[:500],
                }
                for block in self.blocks
            ]
        return payload


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

CURRENCY_MAP = {
    "美元": "USD",
    "美金": "USD",
    "US$": "USD",
    "$": "USD",
    "USD": "USD",
    "港元": "HKD",
    "港幣": "HKD",
    "港币": "HKD",
    "HK$": "HKD",
    "HKD": "HKD",
    "人民幣": "CNY",
    "人民币": "CNY",
    "RMB": "CNY",
    "CNY": "CNY",
    "歐元": "EUR",
    "欧元": "EUR",
    "EUR": "EUR",
    "英鎊": "GBP",
    "英镑": "GBP",
    "GBP": "GBP",
    "日圓": "JPY",
    "日元": "JPY",
    "JPY": "JPY",
    "澳元": "AUD",
    "AUD": "AUD",
    "加元": "CAD",
    "CAD": "CAD",
}

CURRENCY_PATTERN = (
    r"(?:美元|美金|港元|港幣|港币|人民幣|人民币|歐元|欧元|英鎊|英镑|"
    r"日圓|日元|澳元|加元|USD|HKD|RMB|CNY|EUR|GBP|JPY|AUD|CAD|US\$|HK\$)"
)

UNIT_MAP = {
    "百萬": 1_000_000.0,
    "百万": 1_000_000.0,
    "million": 1_000_000.0,
    "mn": 1_000_000.0,
    "億": 100_000_000.0,
    "亿": 100_000_000.0,
    "billion": 1_000_000_000.0,
    "bn": 1_000_000_000.0,
    "萬": 10_000.0,
    "万": 10_000.0,
}

UNIT_PATTERN = r"(?:百萬|百万|million|mn|億|亿|billion|bn|萬|万)"

STRONG_SIZE_LABELS = (
    "總資產值",
    "总资产值",
    "基金規模",
    "基金规模",
    "基金總值",
    "基金总值",
    "基金資產淨值",
    "基金资产净值",
    "總資產淨值",
    "总资产净值",
    "Fund Size",
    "Total Net Assets",
    "Net Assets",
    "AUM",
)

# 这些标签会被单位净值、派息和区间高低点复用。只有整格完全匹配且没有
# “每单位/最高/最低/12个月”等修饰时才允许进入规模解析。
WEAK_SIZE_LABELS = (
    "資產淨值",
    "资产净值",
    "Net Asset Value",
    "NAV",
)

SIZE_LABELS = STRONG_SIZE_LABELS + WEAK_SIZE_LABELS
WEAK_SIZE_CONTEXT = (
    "每單位",
    "每单位",
    "最高",
    "最低",
    "12個月",
    "12个月",
    "派息",
    "贖回",
    "赎回",
    "認購",
    "认购",
)

DURATION_LABELS = (
    "平均年期",
    "平均屆滿期",
    "平均届满期",
    "平均存續期",
    "平均存续期",
    "組合久期",
    "组合久期",
    "加權平均存續期",
    "加权平均存续期",
    "Duration",
    "Average duration",
)

YTM_LABELS = (
    "平均最低孳息率",
    "最低孳息率",
    "平均到期孳息率",
    "到期孳息率",
    "平均到期收益率",
    "平均收益率",
    "期滿收益率",
    "期满收益率",
    "期滿回報率",
    "期满回报率",
    "組合到期收益率",
    "组合到期收益率",
    "到期收益率",
    "最差收益率",
    "加權平均最差收益率",
    "加权平均最差收益率",
    "Yield to maturity",
    "Yield to worst",
    "YTM",
    "YTW",
)

AVERAGE_RATING_LABELS = (
    "平均信用評級",
    "平均信用评级",
    "平均信貸評級",
    "平均信贷评级",
    "Average rating",
    "Average credit rating",
)

RATING_DISTRIBUTION_LABELS = (
    "債券質素",
    "债券质素",
    "債券品質",
    "债券品质",
    "信用評級分佈",
    "信用评级分布",
    "信用評級分佈圖",
    "信用评级分布图",
    "信貸評級分佈",
    "信贷评级分布",
    "Credit quality",
    "Rating distribution",
    "Credit rating distribution",
)

RATING_LABELS = AVERAGE_RATING_LABELS + RATING_DISTRIBUTION_LABELS

DISCLOSURE_LABELS: dict[str, tuple[str, ...]] = {
    "top_holdings": (
        "十大投資項目",
        "十大投资项目",
        "十大持倉",
        "十大持仓",
        "十大持股",
        "十大債券",
        "十大债券",
        "主要持倉",
        "主要持仓",
        "主要持股",
        "主要債券",
        "主要债券",
        "Top 10 holdings",
        "Top holdings",
    ),
    "duration_distribution": (
        "久期分佈",
        "久期分布",
        "存續期分佈",
        "存续期分布",
        "期限分佈",
        "期限分布",
        "Duration distribution",
        "Maturity distribution",
    ),
    "geographic_allocation": (
        "地區分佈",
        "地区分布",
        "國家分佈",
        "国家分布",
        "市場分佈",
        "市场分布",
        "地區配置",
        "地区配置",
        "國家配置",
        "国家配置",
        "市場配置",
        "市场配置",
        "Geographic allocation",
        "Country allocation",
    ),
    "sector_allocation": (
        "行業分佈",
        "行业分布",
        "產業分佈",
        "产业分布",
        "行業配置",
        "行业配置",
        "產業配置",
        "产业配置",
        "Sector allocation",
    ),
}

RATING_TOKEN = (
    r"(?:AAA|AA\+|AA-|AA|A\+|A-|A|BBB\+|BBB-|BBB|BB\+|BB-|BB|"
    r"B\+|B-|B|CCC\+|CCC-|CCC|CC|C|D|NR|"
    r"未評級|未评级|Not Rated|N\.R\.)"
)

RATING_BUCKET_TOKEN = rf"(?:≤|<=|<)?\s*{RATING_TOKEN}"


def normalize_text(value: Any) -> str:
    """NFKC and whitespace normalization; keeps Chinese characters."""

    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def compact_text(value: Any) -> str:
    """Normalize for label matching: casefold and remove punctuation/spaces."""

    text = normalize_text(value).casefold()
    return re.sub(r"[\s\-_/\\()（）【】\[\]:：,，.。;；|]+", "", text)


def normalize_currency(value: Any) -> str | None:
    text = normalize_text(value).upper()
    return CURRENCY_MAP.get(text)


def unit_multiplier(value: Any) -> float | None:
    text = normalize_text(value).casefold()
    for token, multiplier in UNIT_MAP.items():
        if token.casefold() in text:
            return multiplier
    return None


def parse_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?[0-9][0-9,]*(?:\.[0-9]+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _parse_bbox(value: Any) -> tuple[float, float, float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            return tuple(float(item) for item in value)  # type: ignore[return-value]
        except (TypeError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return _parse_bbox(parsed)
    return None


# ---------------------------------------------------------------------------
# Evidence index
# ---------------------------------------------------------------------------


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"}:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None:
            if self._row is None:
                self._row = []
            self._row.append(normalize_text("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(cell for cell in self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def parse_html_table(markup: str) -> list[list[str]]:
    parser = _TableHTMLParser()
    try:
        parser.feed(markup)
    except Exception:  # noqa: BLE001 - malformed HTML is common
        return []
    return parser.rows


def parse_markdown_table(block: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells):
            continue
        rows.append(cells)
    return rows


def _markdown_blocks(markdown: str) -> list[tuple[str, str]]:
    """Split Markdown into (kind, text), keeping HTML tables intact."""

    blocks: list[tuple[str, str]] = []
    buffer: list[str] = []
    in_table = False

    def flush() -> None:
        text = "\n".join(buffer).strip()
        buffer.clear()
        if not text:
            return
        lower = text.casefold()
        if lower.startswith("<table"):
            kind = "table"
        elif text.lstrip().startswith("#"):
            kind = "heading"
        elif "<!-- image" in lower:
            kind = "figure"
        else:
            kind = "text"
        blocks.append((kind, text))

    for line in markdown.splitlines():
        lower = line.casefold()
        if "<table" in lower:
            in_table = True
        if in_table:
            buffer.append(line)
            if "</table>" in lower:
                in_table = False
                flush()
            continue
        if not line.strip():
            flush()
        else:
            buffer.append(line)
    flush()
    return blocks


def build_evidence_index(
    markdown: str,
    *,
    content_list: str | list[dict[str, Any]] | None = None,
    native_pages: Iterable[str] | None = None,
    report_month_hint: str | None = None,
) -> list[EvidenceBlock]:
    """构建可回溯的证据块；参数 markdown/content_list/native_pages 为三类来源。

    native_pages 只保留页级块，避免整页与段落重复命中同一数值；MinerU 缺少
    content_list 时，用原生页文字反查块所在页，避免全部记成第 0 页。
    """

    blocks: list[EvidenceBlock] = []
    native_page_texts = [str(text or "") for text in (native_pages or [])]

    if report_month_hint and str(report_month_hint).strip():
        blocks.append(
            EvidenceBlock(
                page=0,
                index=len(blocks),
                kind="report_hint",
                text=normalize_text(f"[REPORT_MONTH_HINT] {report_month_hint}"),
                source="footer",
            )
        )

    items: list[dict[str, Any]] = []
    if isinstance(content_list, str) and content_list.strip():
        try:
            parsed = json.loads(content_list)
            if isinstance(parsed, list):
                items = [item for item in parsed if isinstance(item, dict)]
        except json.JSONDecodeError:
            items = []
    elif isinstance(content_list, list):
        items = [item for item in content_list if isinstance(item, dict)]

    if items:
        for item in items:
            kind = str(item.get("type") or "text")
            text = normalize_text(item.get("text") or "")
            rows: tuple[tuple[str, ...], ...] = ()
            if kind == "table":
                markup = str(item.get("table_body") or "")
                parsed_rows = parse_html_table(markup)
                rows = tuple(tuple(row) for row in parsed_rows)
                caption = " ".join(
                    str(value)
                    for key in ("table_caption", "table_footnote")
                    for value in (item.get(key) or [])
                )
                text = normalize_text(" ".join([caption, " ".join(" ".join(row) for row in rows)]))
            elif kind == "chart":
                caption = " ".join(
                    str(value)
                    for key in ("chart_caption", "chart_footnote")
                    for value in (item.get(key) or [])
                )
                text = normalize_text(f"{caption} {item.get('content') or ''}")
            if not text and not rows:
                continue
            try:
                page = int(item.get("page_idx") or 0)
            except (TypeError, ValueError):
                page = 0
            blocks.append(
                EvidenceBlock(
                    page=page,
                    index=len(blocks),
                    kind=kind,
                    text=text,
                    bbox=_parse_bbox(item.get("bbox")),
                    source="mineru",
                    rows=rows,
                )
            )
    else:
        for kind, text in _markdown_blocks(markdown):
            rows = tuple(tuple(row) for row in parse_html_table(text)) if kind == "table" else ()
            blocks.append(
                EvidenceBlock(
                    page=0,
                    index=len(blocks),
                    kind=kind,
                    text=normalize_text(text),
                    source="mineru",
                    rows=rows,
                )
            )

    for page_number, page_text in enumerate(native_page_texts):
        text = str(page_text or "").strip()
        if not text:
            continue
        blocks.append(
            EvidenceBlock(
                page=page_number,
                index=len(blocks),
                kind="native_page",
                text=normalize_text(text),
                source="native",
            )
        )

    if items or not native_page_texts:
        return blocks

    # Markdown-only MinerU 批次没有 page_idx。用一段足够长的紧凑文本来反查
    # 原生页；找不到时保留 page=0，并在 evidence source 中仍可区分来源。
    compact_pages = [compact_text(text) for text in native_page_texts]
    for index, block in enumerate(blocks):
        if block.source != "mineru" or block.page != 0:
            continue
        sample = compact_text(block.text)[:80]
        if len(sample) < 16:
            continue
        for page_number, page_text in enumerate(compact_pages):
            if sample in page_text:
                blocks[index] = replace(block, page=page_number)
                break
    return blocks


# ---------------------------------------------------------------------------
# Candidate helpers
# ---------------------------------------------------------------------------


def _evidence(block: EvidenceBlock, quote: str) -> dict[str, Any]:
    return block.ref(quote)


def _prefer_source(block: EvidenceBlock) -> float:
    score = 0.05 if block.page <= 1 else 0.0
    if block.source == "native":
        score += 0.12
    if block.kind == "table":
        score += 0.05
    return score


def _dedupe_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """按值、页码、来源和规则去重，保留置信度最高的证据。"""

    best: dict[tuple[str, int, str, str], Candidate] = {}
    for item in candidates:
        evidence = item.evidence[0] if item.evidence else {}
        key = (
            json.dumps(item.value, ensure_ascii=False, sort_keys=True),
            int(evidence.get("page") or 0),
            str(evidence.get("source") or ""),
            item.rule,
        )
        current = best.get(key)
        if current is None or item.confidence > current.confidence:
            best[key] = item
    return list(best.values())


def _choose(
    field_name: str,
    candidates: list[Candidate],
    *,
    absent_rule: str = "no_anchor",
) -> Candidate:
    if not candidates:
        return Candidate(field=field_name, status=STATUS_ABSENT, rule=absent_rule)

    candidates = _dedupe_candidates(candidates)

    def key(item: Candidate) -> str:
        return json.dumps(item.value, ensure_ascii=False, sort_keys=True)

    grouped: dict[str, list[Candidate]] = {}
    for item in candidates:
        grouped.setdefault(key(item), []).append(item)
    if len(grouped) == 1:
        best = max(candidates, key=lambda item: item.confidence)
        best.confidence = min(0.99, best.confidence)
        return best

    ordered = sorted(candidates, key=lambda item: item.confidence, reverse=True)

    native = [
        item
        for item in candidates
        if any(ref.get("source") == "native" for ref in item.evidence)
    ]
    if native:
        best_native = max(native, key=lambda item: item.confidence)
        if best_native.confidence >= 0.9:
            best_native.status = STATUS_OK
            best_native.meta["alternatives"] = [
                {"value": item.value, "rule": item.rule, "evidence": item.evidence}
                for item in ordered
                if item is not best_native
            ]
            best_native.meta["resolution"] = "native_text_source_priority"
            return best_native

    # 同一字段出现不同值时，先按来源/页眉/表格可信度排序。置信度差距足够大
    # 才允许自动收敛，否则交给 review/local_model，避免“原生层优先”掩盖真冲突。
    best = ordered[0]
    runner_up = ordered[1] if len(ordered) > 1 else None
    if runner_up and best.confidence - runner_up.confidence >= 0.14 and best.confidence >= 0.92:
        best.status = STATUS_OK
        best.meta["alternatives"] = [
            {"value": item.value, "rule": item.rule, "evidence": item.evidence}
            for item in ordered[1:]
        ]
        best.meta["resolution"] = "confidence_margin"
        return best

    best.status = STATUS_CONFLICT
    best.confidence = min(best.confidence, 0.55)
    best.meta["alternatives"] = [
        {"value": item.value, "rule": item.rule, "evidence": item.evidence}
        for item in ordered
        if item is not best
    ]
    return best


# ---------------------------------------------------------------------------
# Fund size
# ---------------------------------------------------------------------------


AMOUNT_PATTERN = r"(?:[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)"
METRIC_AMOUNT_PATTERN = r"(?<![A-Za-z0-9])" + AMOUNT_PATTERN + r"(?!\s*[)）])"


def _size_context_is_safe(label: str, context: str) -> bool:
    """判断规模标签上下文是否安全；参数 label/context 分别为标签和局部原文。"""

    if any(token in context for token in ("不能超逾", "投資限制", "投资限制", "風險", "风险")):
        return False
    if label in WEAK_SIZE_LABELS and any(token in context for token in WEAK_SIZE_CONTEXT):
        return False
    return True


def _fund_size_from_text(text: str, label_text: str = "") -> dict[str, Any] | None:
    """从“标签+金额+币种+单位”的局部窗口解析规模。

    参数 text 为从标签开始的短窗口，label_text 为命中的原始标签。函数故意
    不接受整份报告，也不允许无单位、无币种或用第一个数字兜底，以避免把单位
    NAV、派息、SEDOL/ISIN 误当成基金规模。
    """

    compact = normalize_text(text)
    label = normalize_text(label_text)
    if not compact:
        return None
    if not label:
        found = next(
            (
                item
                for item in SIZE_LABELS
                if re.search(re.escape(item), compact, flags=re.IGNORECASE)
            ),
            "",
        )
        label = found
    if not label:
        return None

    context = compact[: min(len(compact), len(label) + 100)]
    if not _size_context_is_safe(label, context):
        return None

    label_unit_match = re.search(
        r"[（(]\s*(" + UNIT_PATTERN + r")\s*[)）]",
        label,
        flags=re.IGNORECASE,
    )
    label_currency_match = re.search(CURRENCY_PATTERN, label, flags=re.IGNORECASE)
    label_unit = label_unit_match.group(1) if label_unit_match else None
    label_currency = normalize_currency(label_currency_match.group(0)) if label_currency_match else None

    include_label = compact.casefold().startswith(label.casefold())
    base = re.escape(label) + r"\s*" if include_label else ""
    optional_unit = r"(?:[（(]\s*(?P<label_unit>" + UNIT_PATTERN + r")\s*[)）])?\s*"
    amount_then_currency = re.compile(
        base
        + optional_unit
        + r"(?P<gap>[^0-9%]{0,40}?)(?P<amount>"
        + AMOUNT_PATTERN
        + r")\s*(?P<unit>"
        + UNIT_PATTERN
        + r")?\s*(?P<currency>"
        + CURRENCY_PATTERN
        + r")",
        flags=re.IGNORECASE,
    )
    currency_then_amount = re.compile(
        base
        + optional_unit
        + r"(?P<gap>[^0-9%]{0,40}?)(?P<currency>"
        + CURRENCY_PATTERN
        + r")\s*(?P<amount>"
        + AMOUNT_PATTERN
        + r")\s*(?P<unit>"
        + UNIT_PATTERN
        + r")?",
        flags=re.IGNORECASE,
    )

    match = amount_then_currency.search(compact) or currency_then_amount.search(compact)
    if not match:
        return None
    amount = parse_number(match.group("amount"))
    unit = (
        match.groupdict().get("label_unit")
        or match.groupdict().get("unit")
        or label_unit
    )
    currency = normalize_currency(match.groupdict().get("currency") or "") or label_currency
    multiplier = unit_multiplier(unit) if unit else None
    if amount is None or not (0 < amount < 1_000_000_000):
        return None
    if currency is None or multiplier is None:
        return None
    return {
        "amount": amount,
        "currency": currency,
        "unit": unit,
        "multiplier": multiplier,
        "quote": compact[match.start() : min(len(compact), match.end() + 20)],
    }


def _fund_size_from_row(row: Iterable[str]) -> dict[str, Any] | None:
    """从表格行解析规模；参数 row 为单行单元格。"""

    cells = [normalize_text(cell) for cell in row if normalize_text(cell)]
    if len(cells) < 2:
        return None
    joined = " ".join(cells)
    label = ""
    for cell in cells:
        strong = next(
            (item for item in STRONG_SIZE_LABELS if compact_text(item) in compact_text(cell)),
            "",
        )
        if strong:
            label = cell
            break
        weak = next(
            (item for item in WEAK_SIZE_LABELS if compact_text(cell) == compact_text(item)),
            "",
        )
        if weak and _size_context_is_safe(weak, cell):
            label = cell
            break
    if not label:
        return None
    value_cells = [cell for cell in cells if cell != label]
    parsed = next(
        (
            item
            for cell in value_cells
            if _looks_like_size_value(cell)
            for item in [_fund_size_from_text(cell, label)]
            if item
        ),
        None,
    )
    if parsed is None and _looks_like_size_value(joined):
        parsed = _fund_size_from_text(joined, label)
    if parsed is None:
        return None
    parsed["label"] = label
    return parsed


def _looks_like_size_value(value: Any) -> bool:
    """判断单元格是否只包含金额、单位、币种；参数 value 为单元格文本。"""

    compact = normalize_text(value)
    if not compact or not re.search(AMOUNT_PATTERN, compact):
        return False
    if any(token in compact for token in ("類別", "类别", "Class", "class", "累計", "累计")):
        return False
    value_only = (
        r"\s*" + AMOUNT_PATTERN + r"\s*(?:" + UNIT_PATTERN + r")?\s*(?:" + CURRENCY_PATTERN + r")?\s*"
    )
    currency_first = (
        r"\s*(?:" + CURRENCY_PATTERN + r")?\s*" + AMOUNT_PATTERN + r"\s*(?:" + UNIT_PATTERN + r")?\s*"
    )
    return bool(
        re.fullmatch(value_only, compact, flags=re.IGNORECASE)
        or re.fullmatch(currency_first, compact, flags=re.IGNORECASE)
    )


def extract_fund_size(
    blocks: list[EvidenceBlock],
    *,
    fx_rates: dict[str, float] | None = None,
) -> Candidate:
    candidates: list[Candidate] = []
    for block in blocks:
        if block.kind == "table" and block.rows:
            for row_index, row in enumerate(block.rows):
                parsed = _fund_size_from_row(row)
                if not parsed:
                    cells = [normalize_text(cell) for cell in row if normalize_text(cell)]
                    label = next(
                        (
                            cell
                            for cell in cells
                            if any(compact_text(item) in compact_text(cell) for item in STRONG_SIZE_LABELS)
                        ),
                        "",
                    )
                    if label:
                        # 双栏版式常把“總資產值（百萬）”和份额类别放在同一行，
                        # 真实规模落在下一行；只接受严格的值单元格，避免误抓单位 NAV。
                        for follow in block.rows[row_index + 1 : row_index + 4]:
                            value_cell = next(
                                (cell for cell in follow if _looks_like_size_value(cell)),
                                "",
                            )
                            if value_cell:
                                parsed = _fund_size_from_text(value_cell, label)
                                if parsed:
                                    parsed["label"] = label
                                    break
                if not parsed:
                    continue
                value = _size_value(parsed, fx_rates)
                confidence = 0.94 + _prefer_source(block)
                if not (value.get("amount") and value.get("currency") and value.get("multiplier")):
                    confidence = min(confidence, 0.82)
                candidates.append(
                    Candidate(
                        field="fund_size",
                        value=value,
                        status=STATUS_OK,
                        rule="table_label_value",
                        confidence=confidence,
                        evidence=[_evidence(block, parsed["quote"])],
                        meta={"source_text": parsed},
                    )
                )
            continue
        for label in SIZE_LABELS:
            for match in re.finditer(re.escape(label), block.text, flags=re.IGNORECASE):
                context = block.text[max(0, match.start() - 18) : min(len(block.text), match.end() + 24)]
                if not _size_context_is_safe(label, context):
                    continue
                window = block.text[match.start() : min(len(block.text), match.end() + 120)]
                parsed = _fund_size_from_text(window, match.group(0))
                if not parsed:
                    continue
                value = _size_value(parsed, fx_rates)
                confidence = 0.91 + _prefer_source(block)
                if not (value.get("amount") and value.get("currency") and value.get("multiplier")):
                    confidence = min(confidence, 0.82)
                candidates.append(
                    Candidate(
                        field="fund_size",
                        value=value,
                        status=STATUS_OK,
                        rule="text_label_value",
                        confidence=confidence,
                        evidence=[_evidence(block, parsed["quote"])],
                        meta={"source_text": parsed},
                    )
                )
    return _choose("fund_size", candidates)


def _size_value(parsed: dict[str, Any], fx_rates: dict[str, float] | None) -> dict[str, Any]:
    amount = parsed.get("amount")
    currency = parsed.get("currency")
    multiplier = parsed.get("multiplier")
    value: dict[str, Any] = {
        "amount": amount,
        "currency": currency,
        "unit": parsed.get("unit"),
        "multiplier": multiplier,
    }
    rate = None
    if fx_rates and currency:
        rate = fx_rates.get(str(currency))
    if rate is None and currency == "HKD":
        rate = 1.0
    if amount is not None and multiplier and rate is not None:
        value["hkd_100m"] = round(float(amount) * float(multiplier) * float(rate) / 100_000_000, 4)
        value["fx_rate"] = rate
    else:
        value["normalization_error"] = "fx_rate_missing" if amount and multiplier else "unit_or_currency_invalid"
    return value


# ---------------------------------------------------------------------------
# Report month
# ---------------------------------------------------------------------------


MONTH_PATTERN = r"(20\d{2})\s*[年/\-]\s*(\d{1,2})(?:\s*[月/\-]\s*(\d{1,2}))?"
DATE_YEAR_FIRST = r"(?P<year>20\d{2})\s*(?:年|[-/.])\s*(?P<month>\d{1,2})(?:\s*(?:月|[-/.])\s*(?P<day>\d{1,2}))?"
DATE_DAY_FIRST = r"(?P<day_d>\d{1,2})\s*[/.-]\s*(?P<month_d>\d{1,2})\s*[/.-]\s*(?P<year_d>20\d{2})"


def _month(value: tuple[str, str, str | None]) -> str | None:
    try:
        year, month = int(value[0]), int(value[1])
    except (TypeError, ValueError):
        return None
    if not (2000 <= year <= 2099 and 1 <= month <= 12):
        return None
    return f"{year:04d}-{month:02d}"


def _month_from_match(match: re.Match[str]) -> str | None:
    """从命名日期正则结果提取 YYYY-MM；参数 match 为日期匹配对象。"""

    values = match.groupdict()
    if values.get("year"):
        return _month((values.get("year") or "", values.get("month") or "", values.get("day")))
    return _month((values.get("year_d") or "", values.get("month_d") or "", values.get("day_d")))


def _month_from_text(text: str, category: str) -> tuple[str, str, float] | None:
    """从文本提取报告月份及置信度；参数 text/category 为原文与资产类别。"""

    hint = re.search(r"\[REPORT_MONTH_HINT\]\s*(20\d{2})-(\d{2})", text, flags=re.IGNORECASE)
    if hint:
        month = _month((hint.group(1), hint.group(2), None))
        if month:
            return month, hint.group(0), 0.995

    date_pattern = rf"(?:{DATE_YEAR_FIRST}|{DATE_DAY_FIRST})"

    report_header = re.search(
        r"(?:基金報告|基金报告|基金月報|基金月报|月報|月报|Monthly\s+report|Fund\s+report)"
        rf".{{0,36}}?{date_pattern}",
        text,
        flags=re.IGNORECASE,
    )

    as_of = re.search(
        r"(?:截至|截止|資料截至|资料截至|As\s+of|Data\s+as\s+at)"
        rf".{{0,48}}?{date_pattern}",
        text,
        flags=re.IGNORECASE,
    )

    publication = re.search(
        rf"{DATE_YEAR_FIRST}\s*(?:刊|號|号|報|报)",
        text,
        flags=re.IGNORECASE,
    )

    if category == "fixed":
        if as_of:
            month = _month_from_match(as_of)
            if month:
                return month, as_of.group(0), 0.98
        if report_header:
            month = _month_from_match(report_header)
            if month:
                return month, report_header.group(0), 0.95
    else:
        if report_header:
            month = _month_from_match(report_header)
            if month:
                return month, report_header.group(0), 0.98
        if publication:
            month = _month_from_match(publication)
            if month:
                return month, publication.group(0), 0.97
        if as_of:
            month = _month_from_match(as_of)
            if month:
                return month, as_of.group(0), 0.90

    generic = re.search(rf"(?:{DATE_YEAR_FIRST}|{DATE_DAY_FIRST})", text, flags=re.IGNORECASE)
    if generic:
        month = _month_from_match(generic)
        if month:
            return month, generic.group(0), 0.68
    return None


def extract_report_month(blocks: list[EvidenceBlock], *, category: str) -> Candidate:
    candidates: list[Candidate] = []
    for block in blocks:
        result = _month_from_text(block.text, category)
        if not result:
            continue
        month, quote, base_confidence = result
        confidence = base_confidence + (0.01 if block.page == 0 else 0.0)
        if "[REPORT_MONTH_HINT]" in block.text:
            confidence = 0.998
        candidates.append(
            Candidate(
                field="report_date",
                value=month,
                status=STATUS_OK,
                rule="month_hint" if "[REPORT_MONTH_HINT]" in block.text else "month_text",
                confidence=confidence,
                evidence=[_evidence(block, quote)],
            )
        )
    return _choose("report_date", candidates)


# ---------------------------------------------------------------------------
# Duration / YTM
# ---------------------------------------------------------------------------


def _metric_from_row(
    row: Iterable[str],
    *,
    aliases: Iterable[str],
    require_percent: bool,
    exclude: Iterable[str],
) -> float | None:
    cells = [normalize_text(cell) for cell in row if normalize_text(cell)]
    if len(cells) < 2:
        return None
    joined = " ".join(cells)
    if any(compact_text(item) in compact_text(joined) for item in exclude):
        return None
    label = next(
        (cell for cell in cells if any(compact_text(alias) in compact_text(cell) for alias in aliases)),
        "",
    )
    if not label:
        return None
    value_text = " ".join(cell for cell in cells if cell != label)
    return _metric_from_text(value_text, label, require_percent=require_percent)


def _metric_from_text(text: str, label_text: str, *, require_percent: bool) -> float | None:
    """从标签后的短窗口提取久期或收益率；参数 text/label_text/require_percent 为窗口、标签和百分比要求。"""

    compact = normalize_text(text)
    if not compact:
        return None
    tail = compact
    if label_text:
        label_match = re.search(re.escape(normalize_text(label_text)), compact, flags=re.IGNORECASE)
        if label_match:
            tail = compact[label_match.end() : label_match.end() + 80]
        else:
            tail = compact[:80]
    if require_percent:
        has_percent_label = "%" in label_text or "％" in label_text
        match = re.search(METRIC_AMOUNT_PATTERN, tail)
        has_percent_context = bool(match and ("%" in tail[: match.start()] or "％" in tail[: match.start()]))
        if match and not has_percent_label and not has_percent_context:
            match = re.search(METRIC_AMOUNT_PATTERN + r"\s*[%％]", tail)
        value = parse_number(match.group(0)) if match else None
        return value if value is not None and 0 <= value <= 30 else None
    # Duration may be shown as "6.7/10.3"; take the first value.
    number = re.search(METRIC_AMOUNT_PATTERN, tail)
    if not number:
        return None
    # Do not let a bare percentage in a neighbouring column become duration.
    if "%" in tail[: number.start()]:
        return None
    value = parse_number(number.group(0))
    return value if value is not None and 0 <= value <= 30 else None


def extract_metric(
    blocks: list[EvidenceBlock],
    *,
    field_name: str,
    aliases: Iterable[str],
    exclude: Iterable[str],
    require_percent: bool,
) -> Candidate:
    candidates: list[Candidate] = []
    for block in blocks:
        if block.kind == "table" and block.rows:
            for row in block.rows:
                value = _metric_from_row(row, aliases=aliases, require_percent=require_percent, exclude=exclude)
                if value is None:
                    continue
                candidates.append(
                    Candidate(
                        field=field_name,
                        value=value,
                        status=STATUS_OK,
                        rule="table_metric",
                        confidence=0.93 + _prefer_source(block),
                        evidence=[_evidence(block, " | ".join(row))],
                    )
                )
            continue
        for alias in aliases:
            for match in re.finditer(re.escape(alias), block.text, flags=re.IGNORECASE):
                window = block.text[match.start() : min(len(block.text), match.end() + 80)]
                value = _metric_from_text(window, match.group(0), require_percent=require_percent)
                if value is None:
                    continue
                candidates.append(
                    Candidate(
                        field=field_name,
                        value=value,
                        status=STATUS_OK,
                        rule="text_metric",
                        confidence=0.90 + _prefer_source(block),
                        evidence=[_evidence(block, window[:240])],
                    )
                )
    return _choose(field_name, candidates)


# ---------------------------------------------------------------------------
# Credit rating
# ---------------------------------------------------------------------------


def _distribution_pairs(text: str) -> list[tuple[str, float]]:
    """解析评级分布键值对；参数 text 为评级分布附近的原文。"""

    pairs: list[tuple[str, float]] = []
    pattern = re.compile(
        r"(" + RATING_BUCKET_TOKEN + r")\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*[%％]?",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        raw = normalize_text(match.group(1)).upper()
        broad = bool(re.match(r"^(?:<|<=|≤)", raw))
        rating = normalize_rating(re.sub(r"^(?:<|<=|≤)\s*", "", raw))
        weight = parse_number(match.group(2))
        if rating in RATING_RANK and weight is not None and 0 < weight <= 100:
            pairs.append((f"<{rating}" if broad else rating, weight))
    return pairs


def _median_rating(distribution: list[dict[str, Any]]) -> tuple[str | None, str, float]:
    """按累计权重计算中位评级；参数 distribution 为评级和权重列表。

    返回（中位评级、原因、权重合计）。遇到 "<BBB" 这类宽桶跨越 50% 时，
    不猜测桶内具体评级，返回 broad_bucket_straddles_median。
    """

    totals: dict[str, float] = {}
    for item in distribution:
        rating = str(item.get("rating") or "")
        try:
            weight = float(item.get("weight_percent"))
        except (TypeError, ValueError):
            continue
        if weight <= 0:
            continue
        totals[rating] = totals.get(rating, 0.0) + weight
    total = round(sum(totals.values()), 4)
    if total < 50:
        return None, "distribution_below_50", total
    if total > 105:
        return None, "distribution_over_105", total

    exact = [
        (rating, weight)
        for rating, weight in totals.items()
        if not rating.startswith("<") and rating in RATING_RANK
    ]
    exact.sort(key=lambda item: RATING_RANK[item[0]])
    broad = [(rating, weight) for rating, weight in totals.items() if rating.startswith("<")]
    ordered: list[tuple[str, float, bool]] = [
        (rating, weight, False) for rating, weight in exact
    ]
    # 宽桶的内部顺序不可知；把它放在对应边界评级之后，仅当 50% 落在宽桶
    # 内部时拒绝推断。这里不假设 BB+/BB/... 的具体权重。
    for rating, weight in broad:
        boundary = rating[1:]
        position = RATING_RANK.get(boundary, len(RATING_RANK))
        insert_at = len(ordered)
        for index, (exact_rating, _, _) in enumerate(ordered):
            if RATING_RANK.get(exact_rating, len(RATING_RANK)) > position:
                insert_at = index
                break
        ordered.insert(insert_at, (rating, weight, True))

    cumulative = 0.0
    for rating, weight, is_broad in ordered:
        previous = cumulative
        cumulative += weight
        if cumulative >= 50:
            if is_broad:
                return None, "broad_bucket_straddles_median", total
            return rating, "ok" if total >= 90 else "incomplete_distribution", total
        if is_broad and previous < 50 <= cumulative:
            return None, "broad_bucket_straddles_median", total
    return None, "median_not_reached", total


def _distribution_window(text: str) -> str:
    """截取评级分布标签后的局部文本；参数 text 为证据块原文。"""

    label_match = next(
        (
            match
            for label in RATING_DISTRIBUTION_LABELS
            for match in [re.search(re.escape(label), text, flags=re.IGNORECASE)]
            if match
        ),
        None,
    )
    if not label_match:
        return ""
    tail = text[label_match.start() : label_match.start() + 420]
    stop = re.search(
        r"(?:平均年期|平均屆滿期|平均届满期|投資組合分析|投资组合分析|基金表現|基金表现)",
        tail[len(label_match.group(0)) :],
        flags=re.IGNORECASE,
    )
    if stop:
        tail = tail[: len(label_match.group(0)) + stop.start()]
    return tail


def _rating_candidate(
    *,
    pairs: list[tuple[str, float]],
    block: EvidenceBlock,
    quote: str,
    rule: str,
) -> Candidate:
    """把评级分布转成候选；参数 pairs/block/quote/rule 为分布、证据和规则名。"""

    distribution = [{"rating": rating, "weight_percent": weight} for rating, weight in pairs]
    median, reason, total = _median_rating(distribution)
    status = STATUS_OK if median and reason == "ok" else STATUS_UNPARSED
    confidence = 0.93 + _prefer_source(block) if status == STATUS_OK else 0.35
    return Candidate(
        field="credit_rating",
        value=median,
        status=status,
        rule=rule,
        confidence=confidence,
        evidence=[_evidence(block, quote)],
        meta={"distribution": distribution, "distribution_total": total, "reason": reason},
    )


def extract_credit_rating(blocks: list[EvidenceBlock]) -> Candidate:
    average_candidates: list[Candidate] = []
    distribution_candidates: list[Candidate] = []

    for block in blocks:
        if block.kind == "table" and block.rows:
            for row_index, row in enumerate(block.rows):
                cells = [normalize_text(cell) for cell in row if normalize_text(cell)]
                joined = " ".join(cells)
                if any(compact_text(label) in compact_text(joined) for label in AVERAGE_RATING_LABELS):
                    value = next(
                        (
                            normalize_rating(cell)
                            for cell in cells
                            if re.fullmatch(RATING_BUCKET_TOKEN, cell, flags=re.IGNORECASE)
                        ),
                        None,
                    )
                    if value in RATING_RANK:
                        average_candidates.append(
                            Candidate(
                                field="credit_rating",
                                value=value,
                                status=STATUS_OK,
                                rule="average_rating_table",
                                confidence=0.95 + _prefer_source(block),
                                evidence=[_evidence(block, joined)],
                            )
                        )
                if any(compact_text(label) in compact_text(joined) for label in RATING_DISTRIBUTION_LABELS):
                    pairs: list[tuple[str, float]] = []
                    quote_rows: list[str] = []
                    stop_labels = tuple(DURATION_LABELS) + tuple(YTM_LABELS) + ("投資組合分析", "投资组合分析")
                    for follow in block.rows[row_index : row_index + 12]:
                        follow_joined = " ".join(normalize_text(cell) for cell in follow if normalize_text(cell))
                        if quote_rows and any(
                            compact_text(label) in compact_text(follow_joined)
                            for label in stop_labels
                        ):
                            break
                        follow_pairs = _distribution_pairs(follow_joined)
                        if follow_pairs:
                            pairs.extend(follow_pairs)
                            quote_rows.append(follow_joined)
                    if len(pairs) >= 2:
                        distribution_candidates.append(
                            _rating_candidate(
                                pairs=pairs,
                                block=block,
                                quote=" | ".join(quote_rows)[:320],
                                rule="median_rating_table",
                            )
                        )
            continue

        joined = block.text
        for label in AVERAGE_RATING_LABELS:
            label_match = re.search(re.escape(label), joined, flags=re.IGNORECASE)
            if not label_match:
                continue
            tail = joined[label_match.end() : label_match.end() + 40]
            match = re.search(RATING_BUCKET_TOKEN, tail, flags=re.IGNORECASE)
            if match and not re.match(r"^(?:<|<=|≤)", match.group(0)):
                value = normalize_rating(match.group(0))
                if value not in RATING_RANK:
                    continue
                average_candidates.append(
                    Candidate(
                        field="credit_rating",
                        value=value,
                        status=STATUS_OK,
                        rule="average_rating_text",
                        confidence=0.94 + _prefer_source(block),
                        evidence=[_evidence(block, (label + " " + tail)[:240])],
                    )
                )
                break

        window = _distribution_window(joined)
        pairs = _distribution_pairs(window)
        if len(pairs) >= 2:
            distribution_candidates.append(
                _rating_candidate(
                    pairs=pairs,
                    block=block,
                    quote=window[:320],
                    rule="median_rating_text",
                )
            )

    if average_candidates:
        return _choose("credit_rating", average_candidates)
    if distribution_candidates:
        return _choose("credit_rating", distribution_candidates)
    return Candidate(field="credit_rating", status=STATUS_ABSENT, rule="no_rating_anchor")


# ---------------------------------------------------------------------------
# Public API and routing
# ---------------------------------------------------------------------------


def probe_disclosures(blocks: list[EvidenceBlock]) -> dict[str, dict[str, Any]]:
    """检查列表/矩阵字段是否有文本锚点；参数 blocks 为证据块。

    disclosed=False 只代表“文本层没有标题/表头”，用于跳过无效的矩阵文本补提；
    视觉复核仍可继续，不把文本缺失当成业务未披露。
    """

    probes: dict[str, dict[str, Any]] = {}
    for field_name, labels in DISCLOSURE_LABELS.items():
        found = None
        for block in blocks:
            text = compact_text(block.text)
            for label in labels:
                if compact_text(label) in text:
                    found = (block, label)
                    break
            if found:
                break
        if found:
            block, label = found
            start = max(0, block.text.casefold().find(label.casefold()))
            probes[field_name] = {
                "disclosed": True,
                "label": label,
                "evidence": [_evidence(block, block.text[start : start + 160])],
            }
        else:
            probes[field_name] = {"disclosed": False, "label": None, "evidence": []}
    return probes


def extract_local_scalars(
    markdown: str,
    *,
    content_list: str | list[dict[str, Any]] | None = None,
    native_pages: Iterable[str] | None = None,
    category: str = "fixed",
    fx_rates: dict[str, float] | None = None,
    report_month_hint: str | None = None,
) -> LocalExtraction:
    """不调用模型提取确定性标量；参数为 MinerU/原生文本、类别和汇率表。"""

    blocks = build_evidence_index(
        markdown,
        content_list=content_list,
        native_pages=native_pages,
        report_month_hint=report_month_hint,
    )
    fields = {
        "fund_size": extract_fund_size(blocks, fx_rates=fx_rates),
        "report_date": extract_report_month(blocks, category=category),
        "portfolio_duration_years": extract_metric(
            blocks,
            field_name="portfolio_duration_years",
            aliases=DURATION_LABELS,
            exclude=YTM_LABELS,
            require_percent=False,
        ),
        "portfolio_ytm_percent": extract_metric(
            blocks,
            field_name="portfolio_ytm_percent",
            aliases=YTM_LABELS,
            exclude=DURATION_LABELS,
            require_percent=True,
        ),
        "credit_rating": extract_credit_rating(blocks),
    }
    return LocalExtraction(
        fields=fields,
        routing=_route(fields),
        blocks=blocks,
        disclosures=probe_disclosures(blocks),
    )


def _route(fields: dict[str, Candidate]) -> dict[str, list[str]]:
    accept: list[str] = []
    review: list[str] = []
    local_model: list[str] = []
    absent: list[str] = []
    for name, candidate in fields.items():
        if candidate.status == STATUS_ABSENT:
            absent.append(name)
        elif candidate.status == STATUS_OK and candidate.confidence >= 0.9:
            accept.append(name)
        elif candidate.status == STATUS_OK:
            review.append(name)
        else:
            local_model.append(name)
    return {
        "accept": accept,
        "review": review,
        "local_model": local_model,
        "absent": absent,
    }


def _find_markdown(mineru_root: Path, category: str, stem: str) -> Path | None:
    """查找 MinerU Markdown；参数 mineru_root/category/stem 为根目录、类别和文件名。"""

    directories = [mineru_root / category, mineru_root / "markdown" / category]
    for directory in directories:
        if not directory.is_dir():
            continue
        matches = sorted(directory.glob(f"{stem}__*/full.md"))
        if matches:
            return matches[0]
    return None


def _content_list_for(markdown_path: Path) -> str | None:
    artifacts = markdown_path.parent / "artifacts"
    candidates = sorted(artifacts.glob("*_content_list.json")) if artifacts.is_dir() else []
    if not candidates:
        candidates = sorted(artifacts.glob("*_content_list_v2.json")) if artifacts.is_dir() else []
    return candidates[0].read_text(encoding="utf-8") if candidates else None


def _native_pages(pdf_path: Path | None) -> list[str]:
    if pdf_path is None or not pdf_path.is_file():
        return []
    try:
        from pypdf import PdfReader
    except ImportError:
        return []
    try:
        return [page.extract_text() or "" for page in PdfReader(str(pdf_path)).pages]
    except Exception:  # noqa: BLE001
        return []


def native_pages_from_pdf(pdf_path: Path | None) -> list[str]:
    """读取 PDF 原生文字层的逐页文本；参数 pdf_path 为 PDF 路径。"""

    return _native_pages(pdf_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Local-first deterministic scalar extraction")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--category", default="固收")
    parser.add_argument("--mineru-root", type=Path, default=Path("data"))
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--no-native", action="store_true")
    parser.add_argument("--fx-rate", action="append", default=[], metavar="USD=7.8")
    parser.add_argument("--include-blocks", action="store_true")
    args = parser.parse_args()

    category = "fixed" if args.category in {"固收", "fixed", "货币", "money"} else "equity"
    markdown_path = args.markdown or _find_markdown(args.mineru_root, args.category, args.pdf.stem)
    if markdown_path is None or not markdown_path.is_file():
        raise SystemExit(f"MinerU Markdown not found for {args.pdf}")
    markdown = markdown_path.read_text(encoding="utf-8")
    content_list = _content_list_for(markdown_path)
    native_pages = [] if args.no_native else _native_pages(args.pdf)
    fx_rates: dict[str, float] = {}
    for item in args.fx_rate:
        key, _, value = item.partition("=")
        try:
            fx_rates[key.upper().strip()] = float(value)
        except ValueError:
            continue

    result = extract_local_scalars(
        markdown,
        content_list=content_list,
        native_pages=native_pages,
        category=category,
        fx_rates=fx_rates,
    )
    print(json.dumps(result.to_dict(include_blocks=args.include_blocks), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

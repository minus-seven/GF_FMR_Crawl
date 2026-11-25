"""Prepare a target-fund PDF from a manually supplied source.

Some managers publish one semi-annual report containing several sub-funds.  The
task pipeline still expects one PDF per fund, so this module detects pages that
mention the target fund and writes a compact, target-only PDF before MinerU is
called.  A normal single-fund PDF is copied unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader, PdfWriter

from app.services.report_type import detect_report_kind


_GENERIC_TOKENS = {
    "fund",
    "funds",
    "etf",
    "open",
    "ended",
    "openended",
    "company",
    "series",
    "class",
    "listed",
    "accumulating",
    "distributing",
    "share",
    "shares",
    "subfund",
    "subfunds",
}


@dataclass(frozen=True)
class SupplementaryPdfResult:
    """Description of the PDF handed to MinerU."""

    source_pages: int
    matched_pages: tuple[int, ...]
    selected_pages: tuple[int, ...]
    sliced: bool
    match_method: str
    matched_names: tuple[str, ...]
    report_kind: str


def _text_key(value: str | None) -> str:
    """Return a case-insensitive alphanumeric key, retaining CJK characters."""

    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _tokens(value: str | None) -> set[str]:
    """Return meaningful Latin/CJK tokens used for fuzzy name matching."""

    text = str(value or "").casefold()
    words = set(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", text))
    return {word for word in words if word not in _GENERIC_TOKENS}


def _name_matches(page_text: str, aliases: Iterable[str]) -> tuple[bool, str]:
    """Match a page against aliases, allowing ETF/Fund suffix differences."""

    page_key = _text_key(page_text)
    page_tokens = _tokens(page_text)
    for alias in aliases:
        key = _text_key(alias)
        if key and len(key) >= 5 and (key in page_key or page_key in key):
            return True, "name_key"
        alias_tokens = _tokens(alias)
        if not alias_tokens:
            continue
        # A merged report often changes only the legal suffix (Fund/ETF) or
        # inserts a share-class label.  Require all distinctive alias tokens;
        # this prevents a generic "Money Market" hit from selecting peers.
        if alias_tokens.issubset(page_tokens):
            return True, "name_tokens"
    return False, ""


def _read_page_text(reader: PdfReader) -> list[str]:
    """Extract native text page-by-page; MinerU remains the visual fallback."""

    return [page.extract_text() or "" for page in reader.pages]


def _selected_pages(
    pages: list[str],
    aliases: list[str],
) -> tuple[list[int], list[str], str]:
    """Find target pages and add cover/contents pages for report context."""

    matched: list[int] = []
    names: list[str] = []
    method = "none"
    for index, text in enumerate(pages):
        ok, current_method = _name_matches(text, aliases)
        if ok:
            matched.append(index)
            method = current_method
            # Keep a short evidence snippet for the task report, not full text.
            for line in text.splitlines():
                if _name_matches(line, aliases)[0] and line.strip():
                    candidate = " ".join(line.split())
                    if candidate not in names:
                        names.append(candidate[:240])
                    break

    # A source is considered merged only when target-specific pages are a
    # minority.  For a normal one-fund report the complete PDF must be kept.
    merged = len(pages) > 4 and len(matched) >= 2 and len(matched) < len(pages) * 0.8
    if not merged:
        return list(range(len(pages))), names[:8], "full_pdf"

    selected = set(matched)
    # Cover, report title and contents provide the report period and language.
    selected.update(range(min(3, len(pages))))
    return sorted(selected), names[:8], method or "name_key"


def prepare_target_pdf(
    source_pdf: Path,
    destination_pdf: Path,
    *,
    fund_code: str | None = None,
    fund_name_cn: str | None = None,
    fund_name_en: str | None = None,
) -> SupplementaryPdfResult:
    """Copy or slice ``source_pdf`` into a target-fund PDF.

    Matching is deliberately independent of a manager: code, Chinese name and
    English name are all candidates.  English aliases allow Fund/ETF/legal
    suffix variation, which is common in consolidated semi-annual reports.
    """

    reader = PdfReader(str(source_pdf))
    pages = _read_page_text(reader)
    aliases = [value for value in (fund_name_en, fund_name_cn, fund_code) if str(value or "").strip()]
    matched_indexes = [
        index for index, text in enumerate(pages)
        if _name_matches(text, aliases)[0]
    ]
    selected, names, method = _selected_pages(pages, aliases)
    sliced = selected != list(range(len(pages)))
    report_kind = detect_report_kind(
        "\n".join(pages[: min(6, len(pages))]),
        filename=source_pdf.name,
    )

    destination_pdf.parent.mkdir(parents=True, exist_ok=True)
    if not sliced:
        destination_pdf.write_bytes(source_pdf.read_bytes())
    else:
        writer = PdfWriter()
        for index in selected:
            writer.add_page(reader.pages[index])
        with destination_pdf.open("wb") as handle:
            writer.write(handle)

    return SupplementaryPdfResult(
        source_pages=len(pages),
        matched_pages=tuple(index + 1 for index in matched_indexes),
        selected_pages=tuple(index + 1 for index in selected),
        sliced=sliced,
        match_method=method,
        matched_names=tuple(names),
        report_kind=report_kind,
    )

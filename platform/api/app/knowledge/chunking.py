"""Build citation-ready page and chunk records from PDF and MinerU artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
import json
from pathlib import Path
import re
from typing import Any

import pymupdf


MAX_CHUNK_CHARS = 1800
CHUNK_OVERLAP_CHARS = 120


@dataclass(frozen=True)
class PageRecord:
    """Canonical physical page text."""

    page_number: int
    text: str
    width: float | None
    height: float | None
    content_hash: str


@dataclass(frozen=True)
class ChunkRecord:
    """One deterministic evidence fragment."""

    chunk_index: int
    page_number: int
    section_title: str | None
    chunk_type: str
    content: str
    content_hash: str
    metadata: dict[str, Any]


def sha256_text(value: str) -> str:
    """计算文本 SHA-256；参数 value 为待哈希文本。"""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_text(value: Any) -> str:
    """规范化切片文本但保留可读标点；参数 value 为原始字段。"""

    text = html.unescape(str(value or ""))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(?:p|tr|li|h\d)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_text(value: str, *, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """按段落后再按长度切分文本；参数 value/max_chars 为文本和上限。"""

    text = normalize_text(value)
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [part.strip() for part in text.splitlines() if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            start = 0
            while start < len(paragraph):
                end = min(len(paragraph), start + max_chars)
                if end < len(paragraph):
                    boundary = max(
                        paragraph.rfind("。", start, end),
                        paragraph.rfind("；", start, end),
                        paragraph.rfind(" ", start, end),
                    )
                    if boundary > start + max_chars // 2:
                        end = boundary + 1
                chunks.append(paragraph[start:end].strip())
                if end >= len(paragraph):
                    break
                start = max(start + 1, end - CHUNK_OVERLAP_CHARS)
            continue
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = paragraph
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk]


def extract_pdf_pages(pdf_path: Path) -> list[PageRecord]:
    """抽取 PDF 物理页面；参数 pdf_path 为来源 PDF。"""

    document = pymupdf.open(pdf_path)
    pages = []
    try:
        for page_number, page in enumerate(document, start=1):
            text = normalize_text(page.get_text("text"))
            rect = page.rect
            pages.append(
                PageRecord(
                    page_number=page_number,
                    text=text,
                    width=float(rect.width),
                    height=float(rect.height),
                    content_hash=sha256_text(text),
                )
            )
    finally:
        document.close()
    return pages


def _item_context(item: dict[str, Any]) -> str:
    """提取 content_list 条目的可读正文；参数 item 为 MinerU 元素。"""

    item_type = str(item.get("type") or "").lower()
    values: list[Any] = []
    if item_type == "text":
        values.append(item.get("text"))
    elif item_type == "table":
        values.extend(item.get("table_caption") or [])
        values.append(item.get("table_body"))
        values.extend(item.get("table_footnote") or [])
    elif item_type == "chart":
        values.append(item.get("content"))
        values.extend(item.get("chart_caption") or [])
        values.extend(item.get("chart_footnote") or [])
    elif item_type == "image":
        values.append(item.get("content"))
        values.extend(item.get("image_caption") or [])
        values.extend(item.get("image_footnote") or [])
    elif item_type in {"header", "footer", "page_number"}:
        return ""
    else:
        values.append(item.get("text") or item.get("content"))
    return normalize_text("\n".join(str(value) for value in values if value))


def _item_metadata(item: dict[str, Any]) -> dict[str, Any]:
    """保留表格和图表定位元数据；参数 item 为 MinerU 元素。"""

    metadata = {
        "item_type": item.get("type"),
        "bbox": item.get("bbox"),
        "page_idx": item.get("page_idx"),
    }
    for key in (
        "text_level",
        "table_caption",
        "table_body",
        "table_footnote",
        "chart_caption",
        "chart_footnote",
        "image_caption",
        "image_footnote",
        "img_path",
    ):
        if item.get(key):
            metadata[key] = item[key]
    return metadata


def chunks_from_content_list(
    content_list_path: Path,
    *,
    page_count: int,
) -> list[ChunkRecord]:
    """把 MinerU content_list 转为 A 级页级切片；参数为路径和 PDF 页数。"""

    payload = json.loads(content_list_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("content_list must be a JSON array")
    chunks: list[ChunkRecord] = []
    section_title: str | None = None
    for item in payload:
        if not isinstance(item, dict):
            continue
        page_idx = item.get("page_idx")
        if not isinstance(page_idx, int):
            raise ValueError("content_list item is missing integer page_idx")
        page_number = page_idx + 1
        if page_number < 1 or page_number > page_count:
            raise ValueError(
                f"content_list page {page_number} is outside PDF page count {page_count}"
            )
        content = _item_context(item)
        if not content:
            continue
        item_type = str(item.get("type") or "text").lower()
        text_level = item.get("text_level")
        if item_type == "text" and isinstance(text_level, int) and text_level <= 2:
            section_title = content[:300]
        for part in split_text(content):
            chunks.append(
                ChunkRecord(
                    chunk_index=len(chunks),
                    page_number=page_number,
                    section_title=section_title,
                    chunk_type=item_type,
                    content=part,
                    content_hash=sha256_text(
                        f"{page_number}\n{item_type}\n{section_title or ''}\n{part}"
                    ),
                    metadata=_item_metadata(item),
                )
            )
    return chunks


def chunks_from_pdf_pages(
    pages: list[PageRecord],
    *,
    citation_tier: str = "B_pdf_page_map",
) -> list[ChunkRecord]:
    """把 PDF 页文本转为 B 级切片；参数 pages/citation_tier 为页面和等级。"""

    chunks: list[ChunkRecord] = []
    for page in pages:
        for part in split_text(page.text):
            chunks.append(
                ChunkRecord(
                    chunk_index=len(chunks),
                    page_number=page.page_number,
                    section_title=None,
                    chunk_type="page_text",
                    content=part,
                    content_hash=sha256_text(
                        f"{page.page_number}\npage_text\n{part}"
                    ),
                    metadata={
                        "page_width": page.width,
                        "page_height": page.height,
                        "citation_tier": citation_tier,
                    },
                )
            )
    return chunks

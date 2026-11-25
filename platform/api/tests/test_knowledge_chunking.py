"""Unit tests for deterministic page-level chunk construction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.knowledge.chunking import (
    chunks_from_content_list,
    normalize_text,
    split_text,
)


def test_content_list_chunks_keep_page_section_and_table_metadata(
    tmp_path: Path,
) -> None:
    """A级切片应保留页码、章节、表格正文和定位元数据。"""

    path = tmp_path / "fixture_content_list.json"
    path.write_text(
        json.dumps(
            [
                {
                    "type": "text",
                    "text": "投资组合摘要",
                    "text_level": 2,
                    "page_idx": 0,
                    "bbox": [10, 20, 300, 40],
                },
                {
                    "type": "table",
                    "table_caption": ["前十大持仓"],
                    "table_body": (
                        "<table><tr><td>ASML HOLDING NV</td>"
                        "<td>7.86%</td></tr></table>"
                    ),
                    "table_footnote": ["资料来源：基金月报"],
                    "page_idx": 2,
                    "bbox": [10, 100, 500, 300],
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    chunks = chunks_from_content_list(path, page_count=4)

    assert [item.chunk_index for item in chunks] == [0, 1]
    assert chunks[0].page_number == 1
    assert chunks[0].section_title == "投资组合摘要"
    assert chunks[1].page_number == 3
    assert chunks[1].chunk_type == "table"
    assert "ASML HOLDING NV 7.86%" in chunks[1].content
    assert chunks[1].metadata["table_caption"] == ["前十大持仓"]
    assert chunks[1].metadata["bbox"] == [10, 100, 500, 300]


def test_content_list_rejects_missing_or_out_of_range_page(tmp_path: Path) -> None:
    """无页码或页码越界的 content_list 不得生成不可信切片。"""

    path = tmp_path / "fixture_content_list.json"
    path.write_text(
        json.dumps([{"type": "text", "text": "没有页码"}]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="page_idx"):
        chunks_from_content_list(path, page_count=2)

    path.write_text(
        json.dumps([{"type": "text", "text": "越界", "page_idx": 3}]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="outside PDF page count"):
        chunks_from_content_list(path, page_count=2)


def test_text_split_is_bounded_and_normalized() -> None:
    """长文本切片应清理标签并遵守长度上限。"""

    text = normalize_text("<p>第一段</p><br/><p>" + "基金月报内容" * 500 + "</p>")
    chunks = split_text(text, max_chars=500)

    assert len(chunks) > 1
    assert all(len(item) <= 500 for item in chunks)
    assert "<p>" not in "".join(chunks)

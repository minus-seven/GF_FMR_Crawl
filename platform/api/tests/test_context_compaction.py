"""Tests for compact MinerU context construction."""

from __future__ import annotations

import json
import sys
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[3] / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from structured_data.parse_mineru_pdf import load_mineru_bundle


def _bundle_fixture(tmp_path: Path) -> dict[str, object]:
    """创建一个含重复 Markdown 和独立表格的 MinerU 产物目录。"""

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    markdown = "基金資料\n總資產值（百萬） 1,768.3 美元\n\n僅存在於 Markdown 的補充說明段落。"
    (artifacts / "full.md").write_text(markdown, encoding="utf-8")
    items = [
        {"type": "text", "page_idx": 0, "text": "基金資料"},
        {"type": "text", "page_idx": 0, "text": "總資產值（百萬） 1,768.3 美元"},
        {
            "type": "table",
            "page_idx": 1,
            "table_caption": ["地區分佈"],
            "table_body": "<table><tr><td>地區</td><td>比重</td></tr><tr><td>美國</td><td>60.0%</td></tr></table>",
        },
    ]
    (artifacts / "fixture_content_list.json").write_text(
        json.dumps(items, ensure_ascii=False),
        encoding="utf-8",
    )
    return {
        "markdown_path": str(artifacts / "full.md"),
        "artifacts_dir": str(artifacts),
    }


def test_compact_context_removes_json_metadata_and_keeps_page_markers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """紧凑上下文应保留页码、表格和 Markdown 独有正文，去掉 bbox/JSON 噪声。"""

    monkeypatch.setenv("FACTSHEET_CONTEXT_MODE", "compact")
    bundle = load_mineru_bundle(_bundle_fixture(tmp_path))

    context = str(bundle["context"])
    assert bundle["context_mode"] == "compact"
    assert "[PAGE 0]" in context
    assert "[PAGE 1]" in context
    assert "地區分佈" in context
    assert "僅存在於 Markdown 的補充說明段落" in context
    assert "[CONTENT_LIST_JSON]" not in context
    assert "bbox" not in context
    assert len(context) < len(str(bundle["context_full"]))
    assert len(str(bundle["context_sha256"])) == 64


def test_full_context_remains_available_for_ab(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A/B 的 full 变体仍应保留原始 content_list JSON。"""

    monkeypatch.setenv("FACTSHEET_CONTEXT_MODE", "full")
    bundle = load_mineru_bundle(_bundle_fixture(tmp_path))

    assert "[CONTENT_LIST_JSON]" in str(bundle["context"])
    assert bundle["context_mode"] == "full"
    assert len(str(bundle["context"])) == len(str(bundle["context_full"]))

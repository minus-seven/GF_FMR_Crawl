"""Validate the frozen day-one knowledge-base scope and question set."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys

import pymupdf


PROJECT_ROOT = Path(__file__).resolve().parents[3]
ARTIFACT_DIR = PROJECT_ROOT / "docs" / "development" / "artifacts" / "20261026-day1"
SCRIPT_DIR = PROJECT_ROOT / "platform" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import audit_knowledge_readiness as readiness


def _load_json(name: str) -> dict:
    """读取冻结 JSON；参数 name 为文件名。"""

    return json.loads((ARTIFACT_DIR / name).read_text(encoding="utf-8"))


def test_frozen_index_scope_uses_published_pages_only() -> None:
    """冻结范围应排除报告期冲突并只保留有真实 PDF 页码的文档。"""

    scope = _load_json("knowledge-index-scope.json")
    eligible = [item for item in scope["documents"] if item["index_eligible"]]
    excluded = [item for item in scope["documents"] if not item["index_eligible"]]

    assert scope["snapshot_id"] == "b76ef053-f098-4c56-9e17-8e8e61142ec3"
    assert scope["counts"]["documents"] == 104
    assert scope["counts"]["eligible"] == 99
    assert len(eligible) == 99
    assert len(excluded) == 5
    assert {item["report_period_status"] for item in excluded} == {"mismatch"}
    assert all(item["pdf_page_count"] > 0 for item in eligible)
    assert all(item["citation_tier"].startswith(("A_", "B_")) for item in eligible)


def test_initial_question_set_has_required_evidence_contract() -> None:
    """20 题均应具有范围、标准答案和引用或证据搜索范围。"""

    lines = [
        json.loads(line)
        for line in (ARTIFACT_DIR / "qa-20.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    assert len(lines) == 20
    assert len({item["id"] for item in lines}) == 20
    assert {item["category"] for item in lines} >= {
        "numeric_fact",
        "holding",
        "cross_fund_comparison",
        "no_answer",
    }
    for item in lines:
        assert item["question"]
        assert item["expected_answer"]
        assert item["scope"]["snapshot_id"] == (
            "b76ef053-f098-4c56-9e17-8e8e61142ec3"
        )
        assert item["scope"]["report_period"]
        if item["expected_status"] == "answered":
            assert item["evidence"]
            assert all(entry.get("page_number", 0) > 0 for entry in item["evidence"])
        else:
            assert item["evidence_scope"]["reason"]


def test_question_evidence_excerpts_exist_on_cited_pdf_pages() -> None:
    """已答题的每条摘录都必须实际出现在所引用 PDF 页。"""

    audit = _load_json("readiness-audit.json")
    documents = {
        item["document_version_id"]: item
        for item in audit["current_documents"]
        if item["document_version_id"]
    }
    questions = [
        json.loads(line)
        for line in (ARTIFACT_DIR / "qa-20.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    def normalize(value: str) -> str:
        """压缩引用核对文本；参数 value 为 PDF 或标准答案片段。"""

        return re.sub(r"\s+", "", value).casefold()

    for question in questions:
        for evidence in question.get("evidence") or []:
            document = documents[evidence["document_version_id"]]
            resolved = document.get("resolved_file_path")
            pdf_path = Path(resolved)
            pdf = pymupdf.open(pdf_path)
            try:
                page_text = pdf[evidence["page_number"] - 1].get_text("text")
            finally:
                pdf.close()
            assert normalize(evidence["excerpt"]) in normalize(page_text), (
                evidence["document_version_id"],
                evidence["page_number"],
                evidence["excerpt"],
            )


def test_content_list_requires_page_on_every_item(tmp_path: Path) -> None:
    """content_list 有任意条目缺 page_idx 时不得标记为完整页码映射。"""

    path = tmp_path / "sample_content_list.json"
    path.write_text(
        json.dumps(
            [
                {"type": "text", "page_idx": 0, "text": "第一页"},
                {"type": "table", "page_idx": 1, "text": "第二页"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert readiness.inspect_content_list(path)["page_complete"] is True

    path.write_text(
        json.dumps(
            [
                {"type": "text", "page_idx": 0, "text": "第一页"},
                {"type": "table", "text": "缺少页码"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert readiness.inspect_content_list(path)["page_complete"] is False

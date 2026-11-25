"""Database-backed idempotency and retrieval tests for the knowledge index."""

from __future__ import annotations

import os
from pathlib import Path
import uuid

import pytest
from sqlalchemy import func, select


TEST_DATABASE_URL = os.environ.get("PLATFORM_TEST_DATABASE_URL", "")
if TEST_DATABASE_URL:
    os.environ["PLATFORM_DATABASE_URL"] = TEST_DATABASE_URL


pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="PLATFORM_TEST_DATABASE_URL is not configured",
)


def test_current_index_is_idempotent_and_complete() -> None:
    """重复索引不新增切片，所有结果带基金、报告期、页码和路径。"""

    from app.db.session import create_session_factory
    from app.knowledge.indexing import index_current_snapshot
    from app.models import DocumentChunk

    project_root = Path(__file__).resolve().parents[3]
    factory = create_session_factory(TEST_DATABASE_URL)
    with factory() as session:
        snapshot_id, first = index_current_snapshot(
            session,
            project_root=project_root,
        )
        session.commit()
        page_count = session.scalar(
            select(func.count()).select_from(DocumentChunk)
        )
        _, second = index_current_snapshot(
            session,
            project_root=project_root,
        )
        session.commit()
        repeated_count = session.scalar(
            select(func.count()).select_from(DocumentChunk)
        )
        chunks = list(
            session.scalars(
                select(DocumentChunk).where(
                    DocumentChunk.snapshot_id == snapshot_id
                )
            )
        )

    assert first.errors == []
    assert first.documents_indexed > 0
    assert page_count == repeated_count
    assert second.chunks_written == 0
    assert second.pages_written == 0
    assert chunks
    assert all(item.fund_id for item in chunks)
    assert all(item.report_period for item in chunks)
    assert all(item.page_number > 0 for item in chunks)
    assert all(item.source_path for item in chunks)


def test_current_sources_skip_non_pdf_artifacts() -> None:
    """页级知识索引应跳过 Bloomberg CSV 等非 PDF 产物并保留原因。"""

    from app.db.session import create_session_factory
    from app.knowledge.indexing import current_document_sources

    project_root = Path(__file__).resolve().parents[3]
    factory = create_session_factory(TEST_DATABASE_URL)
    with factory() as session:
        _, sources, skipped = current_document_sources(
            session,
            project_root=project_root,
        )

    assert sources
    assert all(item.pdf_path.suffix.lower() == ".pdf" for item in sources)
    assert any(
        item["reason"].startswith("non_pdf_artifact:") for item in skipped
    )


def test_fulltext_search_and_filter_keep_citation_fields() -> None:
    """全文检索应支持基金过滤，并返回完整引用定位字段。"""

    from app.db.session import create_session_factory
    from app.knowledge.search import search_knowledge

    factory = create_session_factory(TEST_DATABASE_URL)
    with factory() as session:
        initial = search_knowledge(
            session,
            query="基金",
            mode="fulltext",
            fund_codes=[],
            report_periods=[],
            limit=5,
        )
        assert initial["items"]
        fund_code = initial["items"][0]["fund_code"]
        filtered = search_knowledge(
            session,
            query="基金",
            mode="fulltext",
            fund_codes=[fund_code],
            report_periods=[],
            limit=5,
        )

    assert filtered["items"]
    assert all(item["fund_code"] == fund_code for item in filtered["items"])
    for item in filtered["items"]:
        assert item["document_version_id"]
        assert item["report_period"]
        assert item["page_number"] > 0
        assert item["source_path"]
        assert item["content_hash"]


def test_hybrid_search_falls_back_to_fulltext_without_vector() -> None:
    """未提供查询向量时，混合模式应明确降级但继续返回全文结果。"""

    from app.db.session import create_session_factory
    from app.knowledge.search import search_knowledge

    factory = create_session_factory(TEST_DATABASE_URL)
    with factory() as session:
        result = search_knowledge(
            session,
            query="基金",
            mode="hybrid",
            fund_codes=[],
            report_periods=[],
            limit=3,
        )

    assert result["mode"] == "fulltext"
    assert result["vector_available"] is False
    assert "vector_unavailable_fallback_to_fulltext" in result["limitations"]
    assert result["items"]


def test_knowledge_search_api_returns_citation_ready_results() -> None:
    """知识检索 API 应返回标准信封和完整页码证据字段。"""

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/api/knowledge/search",
            params={"q": "基金", "mode": "fulltext", "limit": 3},
        )
        vector_response = client.get(
            "/api/knowledge/search",
            params={"q": "基金", "mode": "vector", "limit": 3},
        )
        status_response = client.get("/api/knowledge/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["snapshot_id"]
    assert payload["data"]["mode"] == "fulltext"
    assert payload["data"]["items"]
    for item in payload["data"]["items"]:
        assert item["fund_code"]
        assert item["report_period"]
        assert item["page_number"] > 0
        assert item["source_path"]
        assert item["content_hash"]
    assert vector_response.status_code == 503
    assert (
        vector_response.json()["error"]["code"]
        == "E_KNOWLEDGE_VECTOR_UNAVAILABLE"
    )
    assert status_response.status_code == 200
    status = status_response.json()["data"]
    assert status["documents"] > 0
    assert status["chunks"] > 0
    assert status["pages"] > 0
    assert status["incomplete_chunks"] == 0


def test_historical_index_reuses_single_batch_and_chunks() -> None:
    """历史迁移重复执行时应复用同一批次且不新增页面或切片。"""

    from app.db.session import create_session_factory
    from app.knowledge.indexing import index_historical_assets
    from app.models import DatasetBatch, DocumentChunk, DocumentPage

    project_root = Path(__file__).resolve().parents[3]
    factory = create_session_factory(TEST_DATABASE_URL)
    with factory() as session:
        first_snapshot, first = index_historical_assets(
            session,
            project_root=project_root,
        )
        session.commit()
        page_count = session.scalar(
            select(func.count())
            .select_from(DocumentPage)
            .where(
                DocumentPage.document_version_id.in_(
                    select(DocumentChunk.document_version_id).where(
                        DocumentChunk.snapshot_id == first_snapshot
                    )
                )
            )
        )
        chunk_count = session.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.snapshot_id == first_snapshot)
        )
        second_snapshot, second = index_historical_assets(
            session,
            project_root=project_root,
        )
        session.commit()
        batch_count = session.scalar(
            select(func.count())
            .select_from(DatasetBatch)
            .where(DatasetBatch.source_kind == "knowledge-history")
        )
        repeated_pages = session.scalar(
            select(func.count())
            .select_from(DocumentPage)
            .where(
                DocumentPage.document_version_id.in_(
                    select(DocumentChunk.document_version_id).where(
                        DocumentChunk.snapshot_id == first_snapshot
                    )
                )
            )
        )
        repeated_chunks = session.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.snapshot_id == first_snapshot)
        )

    assert first_snapshot == second_snapshot
    assert first.errors == []
    assert second.errors == []
    assert second.pages_written == 0
    assert second.chunks_written == 0
    assert batch_count == 1
    assert repeated_pages == page_count
    assert repeated_chunks == chunk_count


def test_document_subject_upsert_is_range_idempotent() -> None:
    """同一文档、基金和页范围重复写入时应保留单条 subject。"""

    from app.db.session import create_session_factory
    from app.knowledge.indexing import _upsert_document_subject
    from app.models import DocumentSubject

    factory = create_session_factory(TEST_DATABASE_URL)
    with factory() as session:
        anchor = session.scalar(select(DocumentSubject).limit(1))
        assert anchor is not None
        try:
            for _ in range(2):
                _upsert_document_subject(
                    session,
                    document_version_id=anchor.document_version_id,
                    fund_id=anchor.fund_id,
                    subject_name=anchor.subject_name or "",
                    start_page=9998,
                    end_page=9999,
                    match_method="test_upsert",
                )
                session.flush()
            stored = session.scalar(
                select(DocumentSubject).where(
                    DocumentSubject.document_version_id
                    == anchor.document_version_id,
                    DocumentSubject.fund_id == anchor.fund_id,
                    DocumentSubject.start_page == 9998,
                    DocumentSubject.end_page == 9999,
                )
            )
            stored_method = stored.match_method if stored is not None else None
        finally:
            session.rollback()

    assert stored_method == "test_upsert"


def test_vector_index_and_search_are_deterministic_with_fake_embedding() -> None:
    """向量写入和余弦检索应可重复，且不依赖外部模型运行时。"""

    from app.db.session import create_session_factory
    from app.knowledge.indexing import (
        index_current_snapshot,
        index_embeddings,
        latest_index_run,
    )
    from app.knowledge.search import search_knowledge
    from app.models import ChunkEmbedding

    project_root = Path(__file__).resolve().parents[3]
    model_version = f"test-{uuid.uuid4().hex}"
    factory = create_session_factory(TEST_DATABASE_URL)
    with factory() as session:
        snapshot_id, _ = index_current_snapshot(
            session,
            project_root=project_root,
        )
        session.commit()
        run = latest_index_run(session, snapshot_id=snapshot_id)
        assert run is not None
        first_count = index_embeddings(
            session,
            index_run_id=run.id,
            model_name="test-fake",
            model_version=model_version,
            embed=lambda texts: [
                [1.0] + [0.0] * 1023 for _ in texts
            ],
        )
        session.commit()
        second_count = index_embeddings(
            session,
            index_run_id=run.id,
            model_name="test-fake",
            model_version=model_version,
            embed=lambda texts: [
                [1.0] + [0.0] * 1023 for _ in texts
            ],
        )
        session.commit()
        stored = session.scalar(
            select(func.count())
            .select_from(ChunkEmbedding)
            .where(
                ChunkEmbedding.model_name == "test-fake",
                ChunkEmbedding.model_version == model_version,
            )
        )
        result = search_knowledge(
            session,
            query=None,
            mode="vector",
            fund_codes=[],
            report_periods=[],
            limit=3,
            query_vector=[1.0] + [0.0] * 1023,
            embedding_model="test-fake",
            embedding_version=model_version,
        )

    assert first_count > 0
    assert second_count == 0
    assert stored == first_count
    assert result["vector_available"] is True
    assert result["mode"] == "vector"
    assert len(result["items"]) == 3
    assert all(item["page_number"] > 0 for item in result["items"])

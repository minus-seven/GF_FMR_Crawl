"""Deterministic full-text and optional vector knowledge search."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    ChunkEmbedding,
    DatasetBatch,
    DocumentChunk,
    DocumentPage,
    Fund,
    PublicationState,
    KnowledgeIndexRun,
)


SearchMode = Literal["fulltext", "vector", "hybrid"]


def _published_snapshot(session: Session, snapshot_id: UUID | None) -> UUID:
    """返回允许检索的发布快照；参数 session/snapshot_id 为数据库和目标快照。"""

    if snapshot_id is None:
        snapshot_id = session.scalar(
            select(PublicationState.current_batch_id).where(
                PublicationState.id == 1
            )
        )
    elif session.scalar(
        select(DatasetBatch.status).where(DatasetBatch.id == snapshot_id)
    ) != "published":
        raise PermissionError("knowledge search only allows published snapshots")
    if snapshot_id is None:
        raise LookupError("published snapshot is unavailable")
    return snapshot_id


def _filters(
    statement: Any,
    *,
    snapshot_id: UUID,
    fund_codes: list[str],
    report_periods: list[date],
) -> Any:
    """附加快照、基金和报告期过滤；参数为查询和数据范围。"""

    statement = statement.where(DocumentChunk.snapshot_id == snapshot_id)
    if fund_codes:
        statement = statement.where(Fund.fund_code.in_(fund_codes))
    if report_periods:
        statement = statement.where(
            DocumentChunk.report_period.in_(report_periods)
        )
    return statement


def _result_rows(
    session: Session,
    *,
    statement: Any,
) -> list[Any]:
    """执行结果查询；参数 session/statement 为会话和语句。"""

    return list(session.execute(statement))


def _fulltext_rows(
    session: Session,
    *,
    snapshot_id: UUID,
    query: str | None,
    fund_codes: list[str],
    report_periods: list[date],
    limit: int,
) -> list[Any]:
    """执行全文与关键词召回；参数为检索范围和上限。"""

    score = (
        func.ts_rank_cd(
            DocumentChunk.search_vector,
            func.plainto_tsquery("simple", query),
        )
        + func.similarity(DocumentChunk.content, query) * 0.2
        if query
        else (
            func.similarity(
                func.coalesce(DocumentChunk.section_title, ""),
                query or "",
            )
            * 0
        )
    )
    statement = (
        select(DocumentChunk, Fund, score.label("score"))
        .join(Fund, Fund.id == DocumentChunk.fund_id)
        .order_by(score.desc(), DocumentChunk.page_number, DocumentChunk.chunk_index)
        .limit(limit)
    )
    statement = _filters(
        statement,
        snapshot_id=snapshot_id,
        fund_codes=fund_codes,
        report_periods=report_periods,
    )
    if query:
        statement = statement.where(
            DocumentChunk.search_vector.op("@@")(
                func.plainto_tsquery("simple", query)
            )
            | DocumentChunk.content.ilike(f"%{query}%")
            | func.coalesce(DocumentChunk.section_title, "").ilike(
                f"%{query}%"
            )
        )
    return _result_rows(session, statement=statement)


def _vector_rows(
    session: Session,
    *,
    snapshot_id: UUID,
    query_vector: list[float],
    embedding_model: str,
    embedding_version: str,
    fund_codes: list[str],
    report_periods: list[date],
    limit: int,
) -> list[Any]:
    """执行 pgvector 余弦召回；参数为向量模型、范围和上限。"""

    distance = ChunkEmbedding.embedding.cosine_distance(query_vector)
    score = 1 - distance
    statement = (
        select(DocumentChunk, Fund, score.label("score"))
        .join(Fund, Fund.id == DocumentChunk.fund_id)
        .join(ChunkEmbedding, ChunkEmbedding.chunk_id == DocumentChunk.id)
        .where(
            ChunkEmbedding.model_name == embedding_model,
            ChunkEmbedding.model_version == embedding_version,
        )
        .order_by(distance, DocumentChunk.page_number)
        .limit(limit)
    )
    statement = _filters(
        statement,
        snapshot_id=snapshot_id,
        fund_codes=fund_codes,
        report_periods=report_periods,
    )
    return _result_rows(session, statement=statement)


def _serialize(
    rows: list[Any],
    *,
    scores: dict[UUID, float] | None = None,
) -> list[dict[str, Any]]:
    """把查询行转换为 API 结果；参数 rows/scores 为结果和融合分。"""

    items = []
    for chunk, fund, row_score in rows:
        items.append(
            {
                "chunk_id": chunk.id,
                "fund_id": fund.id,
                "fund_code": fund.fund_code,
                "fund_name": fund.name_cn,
                "report_period": chunk.report_period,
                "document_version_id": chunk.document_version_id,
                "page_number": chunk.page_number,
                "section_title": chunk.section_title,
                "chunk_type": chunk.chunk_type,
                "citation_tier": chunk.citation_tier,
                "content": chunk.content,
                "source_path": chunk.source_path,
                "score": float(
                    scores.get(chunk.id, row_score)
                    if scores is not None
                    else row_score
                ),
                "content_hash": chunk.content_hash,
            }
        )
    return items


def search_knowledge(
    session: Session,
    *,
    query: str | None,
    mode: SearchMode,
    fund_codes: list[str],
    report_periods: list[date],
    limit: int,
    snapshot_id: UUID | None = None,
    query_vector: list[float] | None = None,
    embedding_model: str = "BAAI/bge-m3",
    embedding_version: str = "v1",
) -> dict[str, Any]:
    """执行全文、向量或混合检索；参数为完整检索范围。"""

    snapshot = _published_snapshot(session, snapshot_id)
    vector_available = bool(query_vector)
    if query_vector is not None and len(query_vector) != 1024:
        raise ValueError("query_vector must contain exactly 1024 values")
    if mode == "vector" and not vector_available:
        raise LookupError("vector query requires a 1024-dimensional query_vector")
    limitations: list[str] = []
    if mode == "hybrid" and not vector_available:
        limitations.append("vector_unavailable_fallback_to_fulltext")
        mode = "fulltext"
    fulltext = (
        _fulltext_rows(
            session,
            snapshot_id=snapshot,
            query=query,
            fund_codes=fund_codes,
            report_periods=report_periods,
            limit=limit * 3 if mode == "hybrid" else limit,
        )
        if mode in {"fulltext", "hybrid"}
        else []
    )
    vector = (
        _vector_rows(
            session,
            snapshot_id=snapshot,
            query_vector=query_vector or [],
            embedding_model=embedding_model,
            embedding_version=embedding_version,
            fund_codes=fund_codes,
            report_periods=report_periods,
            limit=limit * 3,
        )
        if mode in {"vector", "hybrid"} and query_vector is not None
        else []
    )
    if mode == "hybrid":
        max_fulltext = max((float(row[2]) for row in fulltext), default=1) or 1
        max_vector = max((float(row[2]) for row in vector), default=1) or 1
        merged: dict[UUID, tuple[Any, float]] = {}
        for row in fulltext:
            chunk = row[0]
            merged[chunk.id] = (row, float(row[2]) / max_fulltext * 0.45)
        for row in vector:
            chunk = row[0]
            current = merged.get(chunk.id)
            combined = float(row[2]) / max_vector * 0.55
            if current:
                combined += current[1]
            merged[chunk.id] = (row, combined)
        rows = [value[0] for value in merged.values()]
        scores = {key: value[1] for key, value in merged.items()}
        rows.sort(key=lambda item: scores[item[0].id], reverse=True)
        items = _serialize(rows[:limit], scores=scores)
    else:
        items = _serialize((vector or fulltext)[:limit])
    return {
        "query": query,
        "mode": mode,
        "vector_available": vector_available,
        "limitations": limitations,
        "items": items,
        "snapshot_id": snapshot,
    }


def knowledge_index_status(session: Session) -> dict[str, Any]:
    """返回当前发布快照的索引质量；参数 session 为数据库会话。"""

    snapshot = _published_snapshot(session, None)
    run = session.scalar(
        select(KnowledgeIndexRun)
        .where(KnowledgeIndexRun.snapshot_id == snapshot)
        .order_by(KnowledgeIndexRun.started_at.desc().nullslast())
        .limit(1)
    )
    documents = session.scalar(
        select(func.count(func.distinct(DocumentChunk.document_version_id)))
        .where(DocumentChunk.snapshot_id == snapshot)
    ) or 0
    funds = session.scalar(
        select(func.count(func.distinct(DocumentChunk.fund_id)))
        .where(DocumentChunk.snapshot_id == snapshot)
    ) or 0
    pages = session.scalar(
        select(func.count())
        .select_from(DocumentPage)
        .where(
            DocumentPage.document_version_id.in_(
                select(DocumentChunk.document_version_id).where(
                    DocumentChunk.snapshot_id == snapshot
                )
            )
        )
    ) or 0
    chunks = session.scalar(
        select(func.count())
        .select_from(DocumentChunk)
        .where(DocumentChunk.snapshot_id == snapshot)
    ) or 0
    embeddings = session.scalar(
        select(func.count())
        .select_from(ChunkEmbedding)
        .join(DocumentChunk, DocumentChunk.id == ChunkEmbedding.chunk_id)
        .where(DocumentChunk.snapshot_id == snapshot)
    ) or 0
    incomplete = session.scalar(
        select(func.count())
        .select_from(DocumentChunk)
        .where(
            DocumentChunk.snapshot_id == snapshot,
            (
                DocumentChunk.fund_id.is_(None)
                | DocumentChunk.page_number.is_(None)
                | DocumentChunk.source_path.is_(None)
            ),
        )
    ) or 0
    tiers = dict(
        session.execute(
            select(DocumentChunk.citation_tier, func.count())
            .where(DocumentChunk.snapshot_id == snapshot)
            .group_by(DocumentChunk.citation_tier)
        ).all()
    )
    return {
        "snapshot_id": snapshot,
        "index_status": run.status if run else None,
        "index_version": run.index_version if run else None,
        "parser_version": run.parser_version if run else None,
        "documents": documents,
        "funds": funds,
        "pages": pages,
        "chunks": chunks,
        "embeddings": embeddings,
        "incomplete_chunks": incomplete,
        "citation_tiers": tiers,
        "vector_model": run.embedding_model if run else None,
        "vector_version": run.embedding_version if run else None,
    }

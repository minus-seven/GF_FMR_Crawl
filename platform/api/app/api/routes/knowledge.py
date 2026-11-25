"""Knowledge search diagnostics endpoint."""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.errors import ApiError
from app.api.responses import envelope
from app.knowledge.search import knowledge_index_status, search_knowledge
from app.schemas.common import ApiResponse
from app.schemas.knowledge import KnowledgeIndexStatusData, KnowledgeSearchData


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("/status", response_model=ApiResponse[KnowledgeIndexStatusData])
def knowledge_status(
    request: Request,
    session: Session = Depends(get_db),
) -> ApiResponse[KnowledgeIndexStatusData]:
    """返回当前发布快照的知识索引质量；参数 request/session 为上下文。"""

    data = knowledge_index_status(session)
    return envelope(request, KnowledgeIndexStatusData(**data), data["snapshot_id"])


@router.get("/search", response_model=ApiResponse[KnowledgeSearchData])
def knowledge_search(
    request: Request,
    q: str | None = Query(None, max_length=500),
    mode: Literal["fulltext", "vector", "hybrid"] = "fulltext",
    fund_codes: list[str] = Query(default=[]),
    report_periods: list[date] = Query(default=[]),
    limit: int = Query(10, ge=1, le=50),
    snapshot_id: UUID | None = None,
    query_vector: list[float] = Query(default=[]),
    embedding_model: str = "BAAI/bge-m3",
    embedding_version: str = "v1",
    session: Session = Depends(get_db),
) -> ApiResponse[KnowledgeSearchData]:
    """检索发布快照中的页级证据；参数为查询范围与数据库会话。"""

    try:
        result = search_knowledge(
            session,
            query=q,
            mode=mode,
            fund_codes=fund_codes,
            report_periods=report_periods,
            limit=limit,
            snapshot_id=snapshot_id,
            query_vector=query_vector or None,
            embedding_model=embedding_model,
            embedding_version=embedding_version,
        )
    except LookupError as exc:
        raise ApiError(
            503,
            "E_KNOWLEDGE_VECTOR_UNAVAILABLE",
            str(exc),
            retryable=False,
        ) from exc
    except ValueError as exc:
        raise ApiError(
            422,
            "E_KNOWLEDGE_QUERY_INVALID",
            str(exc),
            retryable=False,
        ) from exc
    except PermissionError as exc:
        raise ApiError(
            403,
            "E_KNOWLEDGE_SNAPSHOT_NOT_PUBLISHED",
            str(exc),
            retryable=False,
        ) from exc
    snapshot = result.pop("snapshot_id")
    return envelope(request, KnowledgeSearchData(**result), snapshot)

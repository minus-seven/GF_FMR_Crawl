"""ECharts perspective endpoint."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.errors import ApiError
from app.api.responses import envelope
from app.schemas.analytics import PerspectiveData, PerspectiveQuery
from app.schemas.common import ApiResponse
from app.services.queries import get_perspectives


router = APIRouter(prefix="/analytics", tags=["analytics"])


def _get_perspectives(
    request: Request,
    *,
    scope_fund_codes: list[str] | None,
    manager_id: UUID | None,
    category: str | None,
    period: str | None,
    source_document_version_id: UUID | None,
    session: Session,
) -> ApiResponse[PerspectiveData]:
    """统一执行图表查询；GET/POST 仅在传参方式上有差异。"""

    data, snapshot_id = get_perspectives(
        session,
        fund_codes=scope_fund_codes or None,
        manager_id=manager_id,
        category=category,
        period=period,
        source_document_version_id=source_document_version_id,
    )
    return envelope(request, data, snapshot_id)


@router.get(
    "/perspectives",
    response_model=ApiResponse[PerspectiveData],
)
def perspectives(
    request: Request,
    scope_fund_codes: list[str] | None = Query(None, alias="scope_fund_codes"),
    manager_id: UUID | None = None,
    category: str | None = None,
    period: str | None = None,
    source_document_version_id: UUID | None = None,
    session: Session = Depends(get_db),
) -> ApiResponse[PerspectiveData]:
    """返回规模图表与投资组合结构；可按基金详情当前选中的来源版本过滤。"""

    if scope_fund_codes and len(scope_fund_codes) > 10000:
        raise ApiError(
            422,
            "E_SCOPE_TOO_LARGE",
            "基金范围超过 10,000 只",
        )
    return _get_perspectives(
        request,
        scope_fund_codes=scope_fund_codes,
        manager_id=manager_id,
        category=category,
        period=period,
        source_document_version_id=source_document_version_id,
        session=session,
    )


@router.post(
    "/perspectives/query",
    response_model=ApiResponse[PerspectiveData],
)
def perspectives_query(
    payload: PerspectiveQuery,
    request: Request,
    session: Session = Depends(get_db),
) -> ApiResponse[PerspectiveData]:
    """通过 JSON body 查询大范围基金集合，避免 431 Request Header Fields Too Large。"""

    return _get_perspectives(
        request,
        scope_fund_codes=payload.scope_fund_codes,
        manager_id=payload.manager_id,
        category=payload.category,
        period=payload.period,
        source_document_version_id=payload.source_document_version_id,
        session=session,
    )

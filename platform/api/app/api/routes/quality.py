"""Current batch quality issue endpoint."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.responses import envelope
from app.schemas.common import ApiResponse
from app.schemas.funds import (
    QualityPageData,
    QualityReviewActionData,
    QualityReviewData,
    QualityReviewUpdate,
)
from app.services.queries import list_quality_issues
from app.services.quality_review import confirm_review, get_review, save_review


router = APIRouter(prefix="/quality", tags=["quality"])


@router.get("/issues", response_model=ApiResponse[QualityPageData])
def quality_issues(
    request: Request,
    severity: str | None = None,
    status: str | None = None,
    fund_code: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_db),
) -> ApiResponse[QualityPageData]:
    """分页查询当前批次质量问题；参数为过滤和请求上下文。"""

    data, snapshot_id = list_quality_issues(
        session,
        severity=severity,
        status=status,
        fund_code=fund_code,
        page=page,
        page_size=page_size,
    )
    return envelope(request, data, snapshot_id)


@router.get(
    "/issues/{issue_id}/review",
    response_model=ApiResponse[QualityReviewData],
)
def quality_review_detail(
    issue_id: UUID,
    request: Request,
    session: Session = Depends(get_db),
) -> ApiResponse[QualityReviewData]:
    """返回复核工作台所需的基金全量上下文和可编辑记录。"""

    data, snapshot_id = get_review(session, issue_id)
    return envelope(request, data, snapshot_id)


@router.post(
    "/issues/{issue_id}/confirm",
    response_model=ApiResponse[QualityReviewActionData],
)
def quality_review_confirm(
    issue_id: UUID,
    request: Request,
    body: dict | None = None,
    session: Session = Depends(get_db),
) -> ApiResponse[QualityReviewActionData]:
    """确认问题已人工核验；只改变质量状态并留下审计记录。"""

    comment = str((body or {}).get("comment") or "").strip() or None
    data, snapshot_id = confirm_review(session, issue_id, comment)
    return envelope(request, data, snapshot_id)


@router.put(
    "/issues/{issue_id}/review",
    response_model=ApiResponse[QualityReviewActionData],
)
def quality_review_update(
    issue_id: UUID,
    body: QualityReviewUpdate,
    request: Request,
    session: Session = Depends(get_db),
) -> ApiResponse[QualityReviewActionData]:
    """保存编辑后的结构化记录，可选择同时关闭质量问题。"""

    data, snapshot_id = save_review(session, issue_id, body, action="edit")
    return envelope(request, data, snapshot_id)

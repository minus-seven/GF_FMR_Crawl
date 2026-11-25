"""Health and database readiness endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.responses import envelope
from app.models import Fund, FundReportSource
from app.schemas.common import ApiResponse
from app.schemas.health import HealthData


router = APIRouter(tags=["health"])


@router.get("/health", response_model=ApiResponse[HealthData])
def health(
    request: Request,
    session: Session = Depends(get_db),
) -> ApiResponse[HealthData]:
    """检查 API、数据库和当前快照；参数 request/session 为请求上下文。"""

    session.execute(text("SELECT 1"))
    fund_count = int(session.scalar(select(func.count()).select_from(Fund)) or 0)
    source_count = int(
        session.scalar(select(func.count()).select_from(FundReportSource)) or 0
    )
    from app.models import PublicationState

    state = session.get(PublicationState, 1)
    snapshot_id = state.current_batch_id if state else None
    return envelope(
        request,
        HealthData(
            status="ok" if snapshot_id else "degraded",
            database="ok",
            snapshot_id=str(snapshot_id) if snapshot_id else None,
            fund_count=fund_count,
            report_source_count=source_count,
        ),
        snapshot_id,
    )

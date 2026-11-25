"""Current snapshot endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.responses import envelope
from app.schemas.common import ApiResponse
from app.schemas.snapshots import SnapshotData
from app.services.queries import snapshot_data


router = APIRouter(tags=["snapshots"])


@router.get("/snapshots/current", response_model=ApiResponse[SnapshotData])
def current_snapshot(
    request: Request,
    session: Session = Depends(get_db),
) -> ApiResponse[SnapshotData]:
    """返回当前发布快照；参数 request/session 为请求上下文。"""

    data = snapshot_data(session)
    return envelope(request, data, data.snapshot_id)

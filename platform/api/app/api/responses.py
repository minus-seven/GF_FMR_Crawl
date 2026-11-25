"""Helpers for creating the standard API response envelope."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TypeVar
from uuid import UUID

from fastapi import Request

from app.api.deps import get_request_id
from app.schemas.common import ApiResponse, ResponseMeta


T = TypeVar("T")


def envelope(
    request: Request,
    data: T,
    snapshot_id: UUID | str | None,
) -> ApiResponse[T]:
    """构造成功响应；参数 request/data/snapshot_id 为上下文和响应数据。"""

    return ApiResponse(
        data=data,
        snapshot_id=str(snapshot_id) if snapshot_id else None,
        request_id=get_request_id(request),
        meta=ResponseMeta(server_time=datetime.now(timezone.utc)),
    )

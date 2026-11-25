"""Deterministic ECharts perspective response schemas."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import QualityStatus


class PerspectiveView(BaseModel):
    """Dataset for one approved ECharts chart."""

    view_id: str
    title: str
    chart_type: str
    dimension: str
    metric: str
    unit: str
    dataset: list[dict] = Field(default_factory=list)
    source_period: str | None = None
    snapshot_id: str
    quality: QualityStatus


class PerspectiveData(BaseModel):
    """Approved ECharts views for the current scope."""

    views: list[PerspectiveView]
    scope_count: int


class PerspectiveQuery(BaseModel):
    """查询 ECharts 视图的范围；大范围基金集合通过 POST 传递，避免 URL 过长。"""

    scope_fund_codes: list[str] = Field(default_factory=list, max_length=10000)
    manager_id: UUID | None = None
    category: str | None = None
    period: str | None = None
    source_document_version_id: UUID | None = None

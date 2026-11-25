"""Health endpoint schemas."""

from __future__ import annotations

from pydantic import BaseModel


class HealthData(BaseModel):
    """Database and publication health summary."""

    status: str
    database: str
    snapshot_id: str | None
    fund_count: int
    report_source_count: int

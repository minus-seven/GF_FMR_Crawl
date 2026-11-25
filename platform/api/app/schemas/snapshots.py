"""Current snapshot response schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class SnapshotData(BaseModel):
    """Current immutable published snapshot."""

    snapshot_id: UUID
    status: str
    source_kind: str | None
    source_sha256: str | None
    coverage: float | None
    fund_count: int
    published_at: datetime | None
    release_version: str | None

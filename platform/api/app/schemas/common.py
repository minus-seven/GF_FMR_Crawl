"""Common API envelopes, quality states and error payloads."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, Field


T = TypeVar("T")


class QualityStatus(str, Enum):
    """Stable quality states shared by API, ECharts and Agent."""

    COMPLETE = "complete"
    MISSING_FILE = "missing_file"
    MISSING_METRIC = "missing_metric"
    PARSE_FAILED = "parse_failed"
    EXTRACTION_FAILED = "extraction_failed"
    VALIDATION_WARNING = "validation_warning"
    REVIEW_REQUIRED = "review_required"
    STALE = "stale"


class ResponseMeta(BaseModel):
    """Metadata attached to every successful response."""

    server_time: datetime


class ApiResponse(BaseModel, Generic[T]):
    """Versioned response envelope used by all public endpoints."""

    data: T
    snapshot_id: str | None
    request_id: str
    meta: ResponseMeta


class ErrorBody(BaseModel):
    """Stable machine-readable error body."""

    code: str
    message: str
    retryable: bool = False
    details: dict = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    """Error response envelope."""

    error: ErrorBody
    request_id: str

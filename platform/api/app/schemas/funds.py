"""Fund, metric, evidence and quality response schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import QualityStatus


class FundSummary(BaseModel):
    """Fund row used by browse and compare workflows."""

    fund_id: UUID
    fund_code: str
    name_cn: str
    name_en: str | None
    manager_id: UUID
    manager_name: str
    umbrella_name: str | None
    asset_category: str | None
    asset_subcategory: str | None
    status: str
    report_period: date | None
    quality_status: QualityStatus
    latest_scale: float | None = None
    latest_scale_date: date | None = None
    secondary_values: dict[str, str | None] = Field(default_factory=dict)


class FundPageData(BaseModel):
    """Paginated fund result."""

    items: list[FundSummary]
    page: int
    page_size: int
    total: int
    matching_fund_codes: list[str] = Field(default_factory=list)
    secondary_options: list[dict[str, object]] = Field(default_factory=list)


class DownloadSourceOption(BaseModel):
    """One executable source in fallback order."""

    rank: int
    kind: str
    stability: str
    source_url: str
    run_command: str
    reason: str


class ReportSource(BaseModel):
    """Crawler report page and command metadata."""

    source_id: str
    source_sheet: str
    source_row: int
    monthly_report_url: str | None
    fallback_report_url: str | None
    run_command: str | None
    match_status: str
    match_method: str | None
    match_confidence: str | None
    is_active: bool
    source_label: str | None = None
    report_kind: str | None = None
    report_period: date | None = None
    document_version_id: UUID | None = None
    language: str | None = None
    available_sections: list[str] = Field(default_factory=list)
    download_candidates: list[DownloadSourceOption] = Field(default_factory=list)
    is_visible: bool = True
    priority: int = 1
    semiannual_report_url: str | None = None
    semiannual_run_command: str | None = None
    annual_report_url: str | None = None
    annual_run_command: str | None = None


class MetricValue(BaseModel):
    """A single traceable metric value."""

    metric_name: str
    numeric_value: float | None
    text_value: str | None
    unit: str | None
    report_period: date
    review_status: str
    quality_status: QualityStatus
    source_page: int | None
    document_version_id: UUID
    source_label: str | None = None


class MetricListData(BaseModel):
    """Latest metric set for one fund."""

    items: list[MetricValue]
    report_period: date | None
    quality_status: QualityStatus


class HoldingValue(BaseModel):
    """One security holding."""

    security_name: str
    ticker: str | None
    weight: float | None
    market_value: float | None
    holding_quantity: float | None = None
    registration_place: str | None = None
    holding_type: str | None = None
    report_period: date
    source_page: int | None
    source_label: str | None = None


class AllocationValue(BaseModel):
    """One regional, sector, duration or rating allocation."""

    dimension: str
    category: str
    weight: float | None
    unit: str | None
    report_period: date
    source_page: int | None
    source_label: str | None = None


class FundReportData(BaseModel):
    """Structured data grouped by one report period/source."""

    report_period: date
    document_version_id: UUID
    source_label: str | None = None
    report_kind: str | None = None
    metrics: list[MetricValue] = Field(default_factory=list)
    holdings: list[HoldingValue] = Field(default_factory=list)
    allocations: list[AllocationValue] = Field(default_factory=list)


class FundDetailData(BaseModel):
    """Complete deterministic fund detail."""

    fund_id: UUID
    fund_code: str
    name_cn: str
    name_en: str | None
    manager_id: UUID
    manager_name: str
    manager_name_en: str | None
    manager_short_name: str | None
    umbrella_name: str | None
    umbrella_code: str | None
    asset_category: str | None
    asset_subcategory: str | None
    domicile: str | None
    base_currency: str | None
    management_style: str | None
    status: str
    sfc_authorized_at: date | None
    regulation_category: str | None
    tracking_index: str | None
    quality_status: QualityStatus
    report_period: date | None
    metrics: list[MetricValue] = Field(default_factory=list)
    holdings: list[HoldingValue] = Field(default_factory=list)
    allocations: list[AllocationValue] = Field(default_factory=list)
    reports: list[FundReportData] = Field(default_factory=list)
    report_sources: list[ReportSource] = Field(default_factory=list)
    regions: list[dict[str, str]] = Field(default_factory=list)
    tags: list[dict[str, str]] = Field(default_factory=list)
    stock_codes: list[str] = Field(default_factory=list)
    latest_document: dict | None = None


class QualityIssueData(BaseModel):
    """One quality issue returned to the quality page."""

    issue_id: UUID
    fund_id: UUID | None
    fund_code: str | None
    fund_name: str | None
    severity: str
    issue_type: str
    details: str
    status: str
    created_at: datetime
    metadata: dict
    manager_name: str | None = None
    asset_category: str | None = None
    asset_subcategory: str | None = None
    base_currency: str | None = None
    domicile: str | None = None
    fund_status: str | None = None
    report_period: date | None = None


class QualityPageData(BaseModel):
    """Paginated quality issue result."""

    items: list[QualityIssueData]
    page: int
    page_size: int
    total: int


class QualityReviewData(BaseModel):
    """Composite review workbench payload for one quality issue."""

    issue: QualityIssueData
    fund: FundDetailData | None = None
    record: dict[str, Any] = Field(default_factory=dict)
    audit: dict[str, Any] = Field(default_factory=dict)


class QualityReviewUpdate(BaseModel):
    """Editable structured record submitted by the review workbench."""

    record: dict[str, Any] = Field(default_factory=dict)
    comment: str | None = Field(default=None, max_length=2000)
    resolve: bool = True


class QualityReviewActionData(BaseModel):
    """Result of confirming or saving a quality review."""

    issue_id: UUID
    status: str
    updated_at: datetime
    changed_sections: list[str] = Field(default_factory=list)

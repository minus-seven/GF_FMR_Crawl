"""Structured metrics, holdings, allocations and quality issues."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class FundMetric(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Traceable fund metric value linked to a document version."""

    __tablename__ = "fund_metrics"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "fund_id",
            "document_version_id",
            "report_period",
            "metric_name",
            name="uq_fund_metric_value",
        ),
        Index(
            "ix_fund_metrics_lookup",
            "fund_id",
            "report_period",
            "metric_name",
        ),
        CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected')",
            name="ck_fund_metrics_review_status",
        ),
    )

    fund_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    report_period: Mapped[date] = mapped_column(Date, nullable=False)
    metric_name: Mapped[str] = mapped_column(String(120), nullable=False)
    numeric_value: Mapped[float | None] = mapped_column(Numeric(28, 10))
    text_value: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(40))
    confidence: Mapped[str | None] = mapped_column(String(40))
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    source_page: Mapped[int | None]
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class FundHolding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Fund security holding reported in a source document."""

    __tablename__ = "fund_holdings"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "fund_id",
            "document_version_id",
            "report_period",
            "security_name",
            "ticker",
            name="uq_fund_holding_value",
        ),
        Index("ix_fund_holdings_lookup", "fund_id", "report_period"),
    )

    fund_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    report_period: Mapped[date] = mapped_column(Date, nullable=False)
    security_name: Mapped[str] = mapped_column(String(500), nullable=False)
    ticker: Mapped[str | None] = mapped_column(String(100))
    holding_quantity: Mapped[float | None] = mapped_column(Numeric(28, 8))
    registration_place: Mapped[str | None] = mapped_column(String(200))
    holding_type: Mapped[str | None] = mapped_column(String(100))
    weight: Mapped[float | None] = mapped_column(Numeric(12, 8))
    market_value: Mapped[float | None] = mapped_column(Numeric(28, 8))
    source_page: Mapped[int | None]


class FundAllocation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Regional, sector, duration and rating allocation values."""

    __tablename__ = "fund_allocations"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "fund_id",
            "document_version_id",
            "report_period",
            "dimension",
            "category",
            name="uq_fund_allocation_value",
        ),
        Index(
            "ix_fund_allocations_lookup",
            "fund_id",
            "report_period",
            "dimension",
        ),
    )

    fund_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    report_period: Mapped[date] = mapped_column(Date, nullable=False)
    dimension: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(200), nullable=False)
    weight: Mapped[float | None] = mapped_column(Numeric(12, 8))
    unit: Mapped[str | None] = mapped_column(String(40))
    source_page: Mapped[int | None]
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class QualityIssue(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Structured extraction, import and validation issue."""

    __tablename__ = "quality_issues"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('info', 'warning', 'error', 'blocker')",
            name="ck_quality_issue_severity",
        ),
        CheckConstraint(
            "status IN ('open', 'resolved', 'waived')",
            name="ck_quality_issue_status",
        ),
        Index("ix_quality_issues_batch_status", "batch_id", "status"),
        Index("ix_quality_issues_fund", "fund_id"),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    fund_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("funds.id", ondelete="CASCADE"),
    )
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    issue_type: Mapped[str] = mapped_column(String(120), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

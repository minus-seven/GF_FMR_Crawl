"""Fund master data and immutable batch models."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
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


class Manager(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Fund management company master record."""

    __tablename__ = "managers"

    name_cn: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    name_en: Mapped[str | None] = mapped_column(String(300))
    short_name: Mapped[str | None] = mapped_column(String(120))
    ownership_type: Mapped[str | None] = mapped_column(String(80))


class Umbrella(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Umbrella fund grouping."""

    __tablename__ = "umbrellas"
    __table_args__ = (
        UniqueConstraint("umbrella_code", name="uq_umbrella_code"),
    )

    umbrella_code: Mapped[str | None] = mapped_column(String(120))
    name_cn: Mapped[str] = mapped_column(String(300), nullable=False)
    name_en: Mapped[str | None] = mapped_column(String(300))


class DatasetBatch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable or in-progress data batch imported into the platform."""

    __tablename__ = "dataset_batches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('staging', 'published', 'publish_failed', 'failed')",
            name="ck_dataset_batch_status",
        ),
        Index("ix_dataset_batches_status_created", "status", "created_at"),
    )

    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_kind: Mapped[str | None] = mapped_column(String(120))
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    coverage: Mapped[float | None] = mapped_column(Numeric(7, 6))
    manifest_path: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Fund(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Stable fund entity; names are display fields, not identity keys."""

    __tablename__ = "funds"
    __table_args__ = (
        Index("ix_funds_manager_status", "manager_id", "status"),
        Index("ix_funds_name_cn", "name_cn"),
    )

    fund_code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    manager_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("managers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    umbrella_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("umbrellas.id", ondelete="SET NULL"),
    )
    source_batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_batches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name_cn: Mapped[str] = mapped_column(String(400), nullable=False)
    name_en: Mapped[str | None] = mapped_column(String(400))
    domicile: Mapped[str | None] = mapped_column(String(100))
    base_currency: Mapped[str | None] = mapped_column(String(20))
    management_style: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    sfc_authorized_at: Mapped[date | None] = mapped_column(Date)
    regulation_category: Mapped[str | None] = mapped_column(Text)
    financial_year_end: Mapped[str | None] = mapped_column(String(20))
    trading_frequency: Mapped[str | None] = mapped_column(String(40))
    custodian_name: Mapped[str | None] = mapped_column(String(400))
    custodian_short_name: Mapped[str | None] = mapped_column(String(200))
    esg_theme: Mapped[str | None] = mapped_column(String(300))
    esg_strategy: Mapped[str | None] = mapped_column(String(300))
    tracking_index: Mapped[str | None] = mapped_column(String(300))
    listing_status: Mapped[str | None] = mapped_column(String(80))
    leverage_inverse_status: Mapped[str | None] = mapped_column(String(80))
    listed_share_class_status: Mapped[str | None] = mapped_column(String(80))
    api_listing_status: Mapped[str | None] = mapped_column(String(80))
    sixty_forty_classification: Mapped[str | None] = mapped_column(String(80))


class FundSourceId(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Source-system identifiers associated with a canonical fund."""

    __tablename__ = "fund_source_ids"
    __table_args__ = (
        UniqueConstraint(
            "source_system",
            "external_id",
            name="uq_fund_source_external",
        ),
    )

    fund_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_system: Mapped[str] = mapped_column(String(100), nullable=False)
    external_id: Mapped[str] = mapped_column(String(300), nullable=False)
    primary_code: Mapped[str | None] = mapped_column(String(200))
    isin: Mapped[str | None] = mapped_column(String(40))
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class ShareClass(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Fund share class; populated only when the source is unambiguous."""

    __tablename__ = "share_classes"
    __table_args__ = (
        UniqueConstraint(
            "fund_id",
            "share_class_code",
            "currency",
            name="uq_share_class_fund_code_currency",
        ),
    )

    fund_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    share_class_code: Mapped[str] = mapped_column(String(160), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(20))
    isin: Mapped[str | None] = mapped_column(String(40), unique=True)
    distribution_status: Mapped[str | None] = mapped_column(String(80))


class FundListing(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Exchange listing associated with a share class."""

    __tablename__ = "fund_listings"
    __table_args__ = (
        UniqueConstraint(
            "share_class_id",
            "exchange",
            "stock_code",
            name="uq_fund_listing_exchange_code",
        ),
    )

    share_class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("share_classes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(80), nullable=False)
    listing_type: Mapped[str | None] = mapped_column(String(80))
    trading_currency: Mapped[str | None] = mapped_column(String(20))


class FundClassification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Hierarchical fund classifications."""

    __tablename__ = "fund_classifications"
    __table_args__ = (
        UniqueConstraint(
            "fund_id",
            "level_1",
            "level_2",
            "level_3",
            "level_4",
            name="uq_fund_classification_levels",
        ),
    )

    fund_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    level_1: Mapped[str | None] = mapped_column(String(120))
    level_2: Mapped[str | None] = mapped_column(String(120))
    level_3: Mapped[str | None] = mapped_column(String(120))
    level_4: Mapped[str | None] = mapped_column(String(120))


class FundTag(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Business tags with optional validity windows."""

    __tablename__ = "fund_tags"
    __table_args__ = (
        UniqueConstraint(
            "fund_id",
            "tag_type",
            "tag_value",
            "valid_from",
            name="uq_fund_tag_effective",
        ),
    )

    fund_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tag_type: Mapped[str] = mapped_column(String(100), nullable=False)
    tag_value: Mapped[str] = mapped_column(String(200), nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)


class FundRegion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Investment region hierarchy imported from HKEX."""

    __tablename__ = "fund_regions"
    __table_args__ = (
        UniqueConstraint(
            "fund_id",
            "region_level",
            "region_name",
            name="uq_fund_region_level_name",
        ),
    )

    fund_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    region_level: Mapped[str] = mapped_column(String(60), nullable=False)
    region_name: Mapped[str] = mapped_column(String(160), nullable=False)


class FundScaleHistory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Month-end fund size observations."""

    __tablename__ = "fund_scale_history"
    __table_args__ = (
        UniqueConstraint(
            "fund_id",
            "as_of_date",
            "source_batch_id",
            name="uq_fund_scale_batch",
        ),
        Index("ix_fund_scale_fund_date", "fund_id", "as_of_date"),
    )

    fund_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_batches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    fund_size: Mapped[float | None] = mapped_column(Numeric(24, 8))
    currency: Mapped[str | None] = mapped_column(String(20))
    source_unit: Mapped[str | None] = mapped_column(String(40))


class FundNameHistory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Observed fund name changes."""

    __tablename__ = "fund_name_history"

    fund_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_batches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    old_name: Mapped[str] = mapped_column(String(400), nullable=False)
    new_name: Mapped[str] = mapped_column(String(400), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FundReportSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Supplemental report URL and downloader command for a fund."""

    __tablename__ = "fund_report_sources"
    __table_args__ = (
        UniqueConstraint("source_key", name="uq_fund_report_source_key"),
        CheckConstraint(
            "match_status IN ('matched', 'ambiguous', 'unmatched')",
            name="ck_fund_report_source_match_status",
        ),
        Index("ix_fund_report_sources_fund", "fund_id", "is_active"),
    )

    fund_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("funds.id", ondelete="SET NULL"),
    )
    source_system: Mapped[str] = mapped_column(String(100), nullable=False)
    source_key: Mapped[str] = mapped_column(String(300), nullable=False)
    source_sheet: Mapped[str] = mapped_column(String(200), nullable=False)
    source_row: Mapped[int] = mapped_column(nullable=False)
    manager_name_source: Mapped[str | None] = mapped_column(String(300))
    fund_name_source: Mapped[str] = mapped_column(String(400), nullable=False)
    umbrella_name_source: Mapped[str | None] = mapped_column(String(400))
    monthly_report_url: Mapped[str | None] = mapped_column(Text)
    fallback_report_url: Mapped[str | None] = mapped_column(Text)
    semiannual_report_url: Mapped[str | None] = mapped_column(Text)
    semiannual_run_command: Mapped[str | None] = mapped_column(Text)
    annual_report_url: Mapped[str | None] = mapped_column(Text)
    annual_run_command: Mapped[str | None] = mapped_column(Text)
    run_command: Mapped[str | None] = mapped_column(Text)
    report_kind: Mapped[str | None] = mapped_column(String(40))
    priority: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    match_status: Mapped[str] = mapped_column(String(20), nullable=False)
    match_method: Mapped[str | None] = mapped_column(String(80))
    match_confidence: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    is_visible: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[str | None] = mapped_column(String(200))
    delete_reason: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class FundCollection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """用户在基金库页面临时创建的基金集合。"""

    __tablename__ = "fund_collections"
    __table_args__ = (
        Index("ix_fund_collections_session_created", "session_key", "created_at"),
    )

    session_key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(300), nullable=False, default="已选基金集合")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class FundCollectionItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """临时基金集合中的基金成员。"""

    __tablename__ = "fund_collection_items"
    __table_args__ = (
        UniqueConstraint("collection_id", "fund_id", name="uq_fund_collection_item"),
        Index("ix_fund_collection_items_collection", "collection_id"),
    )

    collection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fund_collections.id", ondelete="CASCADE"), nullable=False
    )
    fund_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("funds.id", ondelete="CASCADE"), nullable=False
    )
    fund_code_snapshot: Mapped[str] = mapped_column(String(100), nullable=False)
    fund_name_snapshot: Mapped[str] = mapped_column(String(400), nullable=False)


class SourceManagementAudit(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """数据来源的显示、隐藏和删除审计记录。"""

    __tablename__ = "source_management_audit"

    fund_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("funds.id", ondelete="SET NULL")
    )
    source_key: Mapped[str] = mapped_column(String(300), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(200))
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

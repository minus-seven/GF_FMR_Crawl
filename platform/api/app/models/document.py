"""Document, version and document-to-fund relationship models."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Logical source document shared across versions."""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("source_id", name="uq_documents_source_id"),
    )

    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    umbrella_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("umbrellas.id", ondelete="SET NULL"),
    )
    source_id: Mapped[str] = mapped_column(String(500), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class DocumentVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable version of a source document."""

    __tablename__ = "document_versions"
    __table_args__ = (
        Index("ix_document_versions_document_date", "document_id", "report_date"),
        Index("ix_document_versions_batch", "batch_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_batches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_url: Mapped[str | None] = mapped_column(Text)
    report_date: Mapped[date | None] = mapped_column(Date)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    mime_type: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    file_path: Mapped[str | None] = mapped_column(Text)


class DocumentSubject(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Mapping between a document version and a fund section."""

    __tablename__ = "document_subjects"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "fund_id",
            "start_page",
            "end_page",
            name="uq_document_subject_range",
        ),
        CheckConstraint(
            "start_page IS NULL OR end_page IS NULL OR start_page <= end_page",
            name="ck_document_subject_page_order",
        ),
        Index(
            "ix_document_subjects_fund_version",
            "fund_id",
            "document_version_id",
        ),
    )

    document_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    fund_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
    )
    subject_name: Mapped[str | None] = mapped_column(String(400))
    start_page: Mapped[int | None]
    end_page: Mapped[int | None]
    match_method: Mapped[str | None] = mapped_column(String(80))
    match_score: Mapped[float | None] = mapped_column(Numeric(6, 4))

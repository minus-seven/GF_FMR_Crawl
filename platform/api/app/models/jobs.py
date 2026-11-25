"""D1 task state, lock and publication models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


JOB_STATUS_SQL = (
    "'waiting_lock','queued','running','retrying','validating',"
    "'review_required','publishing','completed','failed','cancelled','publish_failed'"
)


class Job(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A controlled asynchronous platform job."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(f"status IN ({JOB_STATUS_SQL})", name="ck_jobs_status"),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('complete', 'completed_with_gaps', 'partial')",
            name="ck_jobs_outcome",
        ),
        Index("ix_jobs_status_created", "status", "created_at"),
    )

    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(40))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "dataset_batches.id",
            ondelete="SET NULL",
            name="fk_jobs_batch_id",
            use_alter=True,
        ),
        index=True,
    )
    base_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "dataset_batches.id",
            ondelete="SET NULL",
            name="fk_jobs_base_batch_id",
            use_alter=True,
        ),
        index=True,
    )
    requested_stages: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    scope_definition: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    scope_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    task_target: Mapped[str] = mapped_column(String(40), nullable=False, default="fund_monthly", server_default="fund_monthly")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(200))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_requested: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
        server_default="false",
    )
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    result_manifest_path: Mapped[str | None] = mapped_column(Text)
    current_stage: Mapped[str | None] = mapped_column(String(40))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobFundScope(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Frozen fund scope captured when a job is submitted."""

    __tablename__ = "job_fund_scope"
    __table_args__ = (
        UniqueConstraint("job_id", "fund_id", name="uq_job_fund_scope"),
        Index("ix_job_fund_scope_status", "job_id", "result_status"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    fund_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("funds.id", ondelete="RESTRICT"),
        nullable=False,
    )
    fund_code_snapshot: Mapped[str] = mapped_column(String(100), nullable=False)
    fund_name_snapshot: Mapped[str] = mapped_column(String(400), nullable=False)
    result_status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    skip_reason: Mapped[str | None] = mapped_column(Text)


class JobStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Aggregate state for one job stage."""

    __tablename__ = "job_steps"
    __table_args__ = (
        UniqueConstraint("job_id", "step_type", name="uq_job_step_type"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skip_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class JobAttempt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One execution attempt of a job stage."""

    __tablename__ = "job_attempts"
    __table_args__ = (
        UniqueConstraint("job_step_id", "attempt_no", name="uq_job_attempt_number"),
    )

    job_step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_steps.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(80))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Replayable ordered job event."""

    __tablename__ = "job_events"
    __table_args__ = (
        UniqueConstraint("job_id", "sequence_no", name="uq_job_event_sequence"),
        Index("ix_job_events_job_created", "job_id", "created_at"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class JobLock(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Single global write lease."""

    __tablename__ = "job_locks"
    __table_args__ = (
        UniqueConstraint("lock_name", name="uq_job_locks_name"),
        CheckConstraint(
            "lock_name = 'global_write'",
            name="ck_job_locks_global_write",
        ),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    lock_name: Mapped[str] = mapped_column(String(80), nullable=False)
    owner: Mapped[str] = mapped_column(String(200), nullable=False)
    lease_token: Mapped[str] = mapped_column(
        String(64), nullable=False, default=lambda: uuid.uuid4().hex
    )
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PublicationState(TimestampMixin, Base):
    """Single-row pointer to the current published batch."""

    __tablename__ = "publication_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_publication_state_singleton"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    current_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_batches.id", ondelete="RESTRICT"),
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[str | None] = mapped_column(String(200))
    release_version: Mapped[str | None] = mapped_column(String(80))

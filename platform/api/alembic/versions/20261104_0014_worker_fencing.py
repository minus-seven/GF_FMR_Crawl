"""Add worker heartbeat and fencing tokens for resumable jobs."""

from alembic import op
import sqlalchemy as sa


revision = "20261104_0014"
down_revision = "20261104_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add execution heartbeat and lease fencing token columns."""

    inspector = sa.inspect(op.get_bind())
    job_columns = {column["name"] for column in inspector.get_columns("jobs")}
    if "last_heartbeat_at" not in job_columns:
        op.add_column(
            "jobs",
            sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        )
    lock_columns = {column["name"] for column in inspector.get_columns("job_locks")}
    if "lease_token" not in lock_columns:
        op.add_column(
            "job_locks",
            sa.Column("lease_token", sa.String(64), nullable=True),
        )
        op.execute(
            "UPDATE job_locks SET lease_token = md5(random()::text || clock_timestamp()::text) "
            "WHERE lease_token IS NULL"
        )
        op.alter_column("job_locks", "lease_token", nullable=False)


def downgrade() -> None:
    """Remove worker heartbeat and lease fencing token columns."""

    op.drop_column("job_locks", "lease_token")
    op.drop_column("jobs", "last_heartbeat_at")

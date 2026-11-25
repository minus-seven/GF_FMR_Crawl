"""Add the base snapshot reference to jobs.

Revision ID: 20261023_0004
Revises: 20261023_0003
Create Date: 2026-10-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261023_0004"
down_revision = "20261023_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加任务基于的起始发布快照。"""

    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = {column["name"] for column in inspector.get_columns("jobs")}
    if "base_batch_id" not in columns:
        op.add_column(
            "jobs",
            sa.Column(
                "base_batch_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
        )
        op.create_foreign_key(
            "fk_jobs_base_batch_id",
            "jobs",
            "dataset_batches",
            ["base_batch_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index("ix_jobs_base_batch_id", "jobs", ["base_batch_id"])


def downgrade() -> None:
    """移除任务起始快照引用。"""

    op.drop_index("ix_jobs_base_batch_id", table_name="jobs")
    op.drop_constraint("fk_jobs_base_batch_id", "jobs", type_="foreignkey")
    op.drop_column("jobs", "base_batch_id")

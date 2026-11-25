"""Add D3 job control fields.

Revision ID: 20261023_0003
Revises: 20261022_0002
Create Date: 2026-10-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261023_0003"
down_revision = "20261022_0002"
branch_labels = None
depends_on = None


JOB_COLUMNS = {
    "batch_id": sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=True),
    "request_hash": sa.Column("request_hash", sa.String(64), nullable=True),
    "worker_id": sa.Column("worker_id", sa.String(200), nullable=True),
    "cancellation_requested": sa.Column(
        "cancellation_requested",
        sa.Boolean(),
        nullable=False,
        server_default=sa.false(),
    ),
    "error_code": sa.Column("error_code", sa.String(80), nullable=True),
    "error_message": sa.Column("error_message", sa.Text(), nullable=True),
    "result_manifest_path": sa.Column("result_manifest_path", sa.Text(), nullable=True),
}


def upgrade() -> None:
    """为已有数据库补齐 D3 字段。"""

    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing = {column["name"] for column in inspector.get_columns("jobs")}
    for name, column in JOB_COLUMNS.items():
        if name not in existing:
            op.add_column("jobs", column)

    foreign_keys = sa.inspect(connection).get_foreign_keys("jobs")
    has_batch_fk = any(
        item.get("constrained_columns") == ["batch_id"]
        and item.get("referred_table") == "dataset_batches"
        for item in foreign_keys
    )
    if not has_batch_fk:
        op.create_foreign_key(
            "fk_jobs_batch_id",
            "jobs",
            "dataset_batches",
            ["batch_id"],
            ["id"],
            ondelete="SET NULL",
        )
    indexes = {item["name"] for item in sa.inspect(connection).get_indexes("jobs")}
    if "ix_jobs_batch_id" not in indexes:
        op.create_index("ix_jobs_batch_id", "jobs", ["batch_id"])

    op.execute(
        "UPDATE jobs SET request_hash = scope_hash "
        "WHERE request_hash IS NULL"
    )
    op.alter_column("jobs", "request_hash", nullable=False)


def downgrade() -> None:
    """移除 D3 任务控制字段。"""

    op.drop_index("ix_jobs_batch_id", table_name="jobs")
    op.drop_constraint("fk_jobs_batch_id", "jobs", type_="foreignkey")
    for name in reversed(tuple(JOB_COLUMNS)):
        op.drop_column("jobs", name)

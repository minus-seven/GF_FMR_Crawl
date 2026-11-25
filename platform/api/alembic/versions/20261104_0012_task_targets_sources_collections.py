"""Add report-source visibility, task targets and temporary fund collections."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "20261104_0012"
down_revision = "20261103_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增半年报来源字段、可见性管理、任务目标和基金集合表。"""

    op.add_column("fund_report_sources", sa.Column("semiannual_report_url", sa.Text()))
    op.add_column("fund_report_sources", sa.Column("semiannual_run_command", sa.Text()))
    op.add_column("fund_report_sources", sa.Column("report_kind", sa.String(length=40)))
    op.add_column("fund_report_sources", sa.Column("priority", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("fund_report_sources", sa.Column("is_visible", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("fund_report_sources", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column("fund_report_sources", sa.Column("deleted_by", sa.String(length=200)))
    op.add_column("fund_report_sources", sa.Column("delete_reason", sa.Text()))
    op.add_column("jobs", sa.Column("task_target", sa.String(length=40), nullable=False, server_default="fund_monthly"))
    op.create_table(
        "fund_collections",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_key", sa.String(length=200), nullable=False),
        sa.Column("label", sa.String(length=300), nullable=False, server_default="已选基金集合"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fund_collections_session_created", "fund_collections", ["session_key", "created_at"])
    op.create_index("ix_fund_collections_session_key", "fund_collections", ["session_key"])
    op.create_table(
        "fund_collection_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("collection_id", sa.UUID(), nullable=False),
        sa.Column("fund_id", sa.UUID(), nullable=False),
        sa.Column("fund_code_snapshot", sa.String(length=100), nullable=False),
        sa.Column("fund_name_snapshot", sa.String(length=400), nullable=False),
        sa.ForeignKeyConstraint(["collection_id"], ["fund_collections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["fund_id"], ["funds.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("collection_id", "fund_id", name="uq_fund_collection_item"),
    )
    op.create_index("ix_fund_collection_items_collection", "fund_collection_items", ["collection_id"])
    op.create_table(
        "source_management_audit",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fund_id", sa.UUID()),
        sa.Column("source_key", sa.String(length=300), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=200)),
        sa.Column("details", JSONB, nullable=False),
        sa.ForeignKeyConstraint(["fund_id"], ["funds.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """回滚任务目标、临时集合和来源管理字段。"""

    op.drop_table("source_management_audit")
    op.drop_index("ix_fund_collection_items_collection", table_name="fund_collection_items")
    op.drop_table("fund_collection_items")
    op.drop_index("ix_fund_collections_session_key", table_name="fund_collections")
    op.drop_index("ix_fund_collections_session_created", table_name="fund_collections")
    op.drop_table("fund_collections")
    op.drop_column("jobs", "task_target")
    for name in ("delete_reason", "deleted_by", "deleted_at", "is_visible", "priority", "report_kind", "semiannual_run_command", "semiannual_report_url"):
        op.drop_column("fund_report_sources", name)

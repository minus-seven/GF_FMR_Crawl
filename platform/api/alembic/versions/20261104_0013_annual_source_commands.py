"""Add annual report source navigation metadata."""

from alembic import op
import sqlalchemy as sa


revision = "20261104_0013"
down_revision = "20261104_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增年报稳定入口和下载命令字段。"""

    op.add_column("fund_report_sources", sa.Column("annual_report_url", sa.Text()))
    op.add_column("fund_report_sources", sa.Column("annual_run_command", sa.Text()))


def downgrade() -> None:
    """回滚年报稳定入口和下载命令字段。"""

    op.drop_column("fund_report_sources", "annual_run_command")
    op.drop_column("fund_report_sources", "annual_report_url")

"""Store disclosed holding quantities from portfolio tables.

Revision ID: 20261103_0008
Revises: 20261027_0007
Create Date: 2026-11-03
"""

from alembic import op


revision = "20261103_0008"
down_revision = "20261027_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the source-reported holding quantity without changing old rows."""

    op.execute(
        "ALTER TABLE fund_holdings "
        "ADD COLUMN IF NOT EXISTS holding_quantity NUMERIC(28, 8)"
    )


def downgrade() -> None:
    """Remove the optional holding quantity column."""

    op.execute("ALTER TABLE fund_holdings DROP COLUMN IF EXISTS holding_quantity")

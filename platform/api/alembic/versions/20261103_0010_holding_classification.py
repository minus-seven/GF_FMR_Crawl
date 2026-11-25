"""Add holding registration place and instrument type."""

from alembic import op


revision = "20261103_0010"
down_revision = "20261103_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE fund_holdings "
        "ADD COLUMN IF NOT EXISTS registration_place VARCHAR(200)"
    )
    op.execute(
        "ALTER TABLE fund_holdings "
        "ADD COLUMN IF NOT EXISTS holding_type VARCHAR(100)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE fund_holdings DROP COLUMN IF EXISTS holding_type")
    op.execute("ALTER TABLE fund_holdings DROP COLUMN IF EXISTS registration_place")

"""Expose source-reported holding quantities in the current holding view.

Revision ID: 20261103_0009
Revises: 20261103_0008
Create Date: 2026-11-03
"""

from alembic import op


revision = "20261103_0009"
down_revision = "20261103_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Recreate the read-only holding view with the quantity column."""

    op.execute("DROP VIEW IF EXISTS analytics_readonly.fund_holding_current")
    op.execute(
        """
        CREATE VIEW analytics_readonly.fund_holding_current AS
        SELECT
          h.id,
          h.fund_id,
          f.fund_code,
          f.name_cn,
          h.report_period,
          h.security_name,
          h.ticker,
          h.holding_quantity,
          h.weight,
          h.market_value,
          h.source_page,
          h.document_version_id,
          h.batch_id AS snapshot_id
        FROM fund_holdings h
        JOIN publication_state p
          ON p.id = 1
         AND p.current_batch_id = h.batch_id
        JOIN funds f ON f.id = h.fund_id
        """
    )


def downgrade() -> None:
    """Restore the previous view shape without holding quantity."""

    op.execute("DROP VIEW IF EXISTS analytics_readonly.fund_holding_current")
    op.execute(
        """
        CREATE VIEW analytics_readonly.fund_holding_current AS
        SELECT
          h.id, h.fund_id, f.fund_code, f.name_cn, h.report_period,
          h.security_name, h.ticker, h.weight, h.market_value,
          h.source_page, h.document_version_id, h.batch_id AS snapshot_id
        FROM fund_holdings h
        JOIN publication_state p
          ON p.id = 1 AND p.current_batch_id = h.batch_id
        JOIN funds f ON f.id = h.fund_id
        """
    )

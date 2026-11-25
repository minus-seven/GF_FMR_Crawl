"""Create read-only analytics views for ECharts and Tableau Desktop.

Revision ID: 20261022_0002
Revises: 20261022_0001
Create Date: 2026-10-22
"""

from alembic import op


revision = "20261022_0002"
down_revision = "20261022_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建只读分析 schema，不向只读角色暴露任务和凭证表。"""

    op.execute("CREATE SCHEMA IF NOT EXISTS analytics_readonly")
    op.execute(
        """
        CREATE OR REPLACE VIEW analytics_readonly.current_snapshot AS
        SELECT
          p.current_batch_id AS snapshot_id,
          p.published_at,
          p.release_version,
          b.source_kind,
          b.source_sha256,
          b.coverage
        FROM publication_state p
        JOIN dataset_batches b ON b.id = p.current_batch_id
        WHERE p.id = 1
          AND b.status = 'published'
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW analytics_readonly.fund_summary AS
        SELECT
          f.id AS fund_id,
          f.fund_code,
          f.name_cn,
          f.name_en,
          f.manager_id,
          m.name_cn AS manager_name,
          m.short_name AS manager_short_name,
          f.umbrella_id,
          u.umbrella_code,
          u.name_cn AS umbrella_name,
          c.level_1 AS asset_category,
          c.level_2 AS asset_subcategory,
          f.domicile,
          f.base_currency,
          f.management_style,
          f.status,
          f.sfc_authorized_at,
          f.tracking_index,
          f.listing_status
        FROM funds f
        JOIN managers m ON m.id = f.manager_id
        LEFT JOIN umbrellas u ON u.id = f.umbrella_id
        LEFT JOIN fund_classifications c ON c.fund_id = f.id
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW analytics_readonly.fund_scale_current AS
        SELECT
          s.id,
          s.fund_id,
          f.fund_code,
          f.name_cn,
          s.as_of_date,
          s.fund_size,
          s.currency,
          s.source_unit,
          s.source_batch_id AS snapshot_id
        FROM fund_scale_history s
        JOIN publication_state p
          ON p.id = 1
         AND p.current_batch_id = s.source_batch_id
        JOIN funds f ON f.id = s.fund_id
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW analytics_readonly.fund_metric_current AS
        SELECT
          fm.id,
          fm.fund_id,
          f.fund_code,
          f.name_cn,
          fm.report_period,
          fm.metric_name,
          fm.numeric_value,
          fm.text_value,
          fm.unit,
          fm.confidence,
          fm.review_status,
          fm.source_page,
          fm.document_version_id,
          fm.batch_id AS snapshot_id
        FROM fund_metrics fm
        JOIN publication_state p
          ON p.id = 1
         AND p.current_batch_id = fm.batch_id
        JOIN funds f ON f.id = fm.fund_id
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW analytics_readonly.fund_holding_current AS
        SELECT
          h.id,
          h.fund_id,
          f.fund_code,
          f.name_cn,
          h.report_period,
          h.security_name,
          h.ticker,
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
    op.execute(
        """
        CREATE OR REPLACE VIEW analytics_readonly.fund_allocation_current AS
        SELECT
          a.id,
          a.fund_id,
          f.fund_code,
          f.name_cn,
          a.report_period,
          a.dimension,
          a.category,
          a.weight,
          a.unit,
          a.source_page,
          a.document_version_id,
          a.batch_id AS snapshot_id
        FROM fund_allocations a
        JOIN publication_state p
          ON p.id = 1
         AND p.current_batch_id = a.batch_id
        JOIN funds f ON f.id = a.fund_id
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW analytics_readonly.quality_issue_current AS
        SELECT
          q.id,
          q.fund_id,
          f.fund_code,
          f.name_cn,
          q.severity,
          q.issue_type,
          q.details,
          q.status,
          q.created_at,
          q.batch_id AS snapshot_id
        FROM quality_issues q
        JOIN publication_state p
          ON p.id = 1
         AND p.current_batch_id = q.batch_id
        LEFT JOIN funds f ON f.id = q.fund_id
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW analytics_readonly.fund_report_source AS
        SELECT
          rs.id,
          rs.fund_id,
          f.fund_code,
          f.name_cn,
          rs.source_system,
          rs.source_key,
          rs.source_sheet,
          rs.source_row,
          rs.monthly_report_url,
          rs.fallback_report_url,
          rs.run_command,
          rs.match_status,
          rs.match_method,
          rs.match_confidence,
          rs.is_active
        FROM fund_report_sources rs
        JOIN funds f ON f.id = rs.fund_id
        """
    )
    op.execute("REVOKE ALL ON SCHEMA analytics_readonly FROM PUBLIC")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fund_research_ro') THEN
            GRANT USAGE ON SCHEMA analytics_readonly TO fund_research_ro;
            GRANT SELECT ON ALL TABLES IN SCHEMA analytics_readonly
              TO fund_research_ro;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    """删除只读分析视图。"""

    op.execute("DROP SCHEMA IF EXISTS analytics_readonly CASCADE")

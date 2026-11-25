"""Verify D2 views, public data and read-only role boundaries."""

from __future__ import annotations

import argparse
import json

from sqlalchemy import func, select, text

from app.db.session import create_session_factory
from app.models import Fund, PublicationState


EXPECTED_VIEWS = {
    "current_snapshot",
    "fund_summary",
    "fund_scale_current",
    "fund_metric_current",
    "fund_holding_current",
    "fund_allocation_current",
    "quality_issue_current",
    "fund_report_source",
}


def parse_args() -> argparse.Namespace:
    """解析 D2 核验参数；参数为空。"""

    parser = argparse.ArgumentParser(description="核验 D2 API 和只读视图。")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--readonly-role", default="fund_research_ro")
    return parser.parse_args()


def main() -> int:
    """执行 D2 数据库侧验收；参数为空。"""

    args = parse_args()
    factory = create_session_factory(args.database_url)
    with factory() as session:
        views = set(
            session.scalars(
                text(
                    """
                    SELECT table_name
                    FROM information_schema.views
                    WHERE table_schema = 'analytics_readonly'
                    """
                )
            )
        )
        fund_count = int(session.scalar(select(func.count()).select_from(Fund)) or 0)
        state = session.get(PublicationState, 1)
        view_count = int(
            session.scalar(text("SELECT count(*) FROM analytics_readonly.fund_summary"))
            or 0
        )
        scale_count = int(
            session.scalar(
                text("SELECT count(*) FROM analytics_readonly.fund_scale_current")
            )
            or 0
        )
        can_read_view = bool(
            session.scalar(
                text(
                    """
                    SELECT has_table_privilege(
                      :role,
                      'analytics_readonly.fund_summary',
                      'SELECT'
                    )
                    """
                ),
                {"role": args.readonly_role},
            )
        )
        can_read_jobs = bool(
            session.scalar(
                text(
                    """
                    SELECT has_table_privilege(:role, 'public.jobs', 'SELECT')
                    """
                ),
                {"role": args.readonly_role},
            )
        )
    result = {
        "snapshot_id": str(state.current_batch_id) if state else None,
        "funds": fund_count,
        "fund_summary_view_rows": view_count,
        "fund_scale_current_rows": scale_count,
        "views": sorted(views),
        "readonly_can_read_analytics_view": can_read_view,
        "readonly_can_read_jobs": can_read_jobs,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    failures = []
    if fund_count != 2479 or view_count != 2479:
        failures.append("fund count mismatch")
    if not EXPECTED_VIEWS.issubset(views):
        failures.append("analytics views missing")
    if not can_read_view:
        failures.append("readonly role cannot read analytics view")
    if can_read_jobs:
        failures.append("readonly role can read internal jobs")
    if failures:
        print("[FAILED] " + "; ".join(failures))
        return 2
    print("[OK] D2 verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

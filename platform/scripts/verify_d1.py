"""Verify D1 row counts and current publication pointer."""

from __future__ import annotations

import argparse
import json

from sqlalchemy import func, select

from app.db.session import create_session_factory
from app.models import (
    DatasetBatch,
    Fund,
    FundAllocation,
    FundHolding,
    FundMetric,
    FundReportSource,
    PublicationState,
    QualityIssue,
)


def parse_args() -> argparse.Namespace:
    """解析校验参数；参数为空。"""

    parser = argparse.ArgumentParser(description="核验 D1 PostgreSQL 数据底座。")
    parser.add_argument(
        "--database-url",
        required=True,
        help="SQLAlchemy PostgreSQL 连接地址。",
    )
    return parser.parse_args()


def main() -> int:
    """输出关键表行数和当前批次；参数为空。"""

    args = parse_args()
    factory = create_session_factory(args.database_url)
    with factory() as session:
        state = session.get(PublicationState, 1)
        current_batch_id = (
            state.current_batch_id if state and state.current_batch_id else None
        )
        counts = {
            "funds": session.scalar(select(func.count()).select_from(Fund)),
            "metrics": session.scalar(
                select(func.count())
                .select_from(FundMetric)
                .where(FundMetric.batch_id == current_batch_id)
            ),
            "holdings": session.scalar(
                select(func.count())
                .select_from(FundHolding)
                .where(FundHolding.batch_id == current_batch_id)
            ),
            "allocations": session.scalar(
                select(func.count())
                .select_from(FundAllocation)
                .where(FundAllocation.batch_id == current_batch_id)
            ),
            "quality_issues": session.scalar(
                select(func.count())
                .select_from(QualityIssue)
                .where(QualityIssue.batch_id == current_batch_id)
            ),
            "report_sources": session.scalar(
                select(func.count()).select_from(FundReportSource)
            ),
            "matched_report_sources": session.scalar(
                select(func.count())
                .select_from(FundReportSource)
                .where(FundReportSource.match_status == "matched")
            ),
            "batches": session.scalar(select(func.count()).select_from(DatasetBatch)),
        }
        counts["current_batch_id"] = (
            str(current_batch_id) if current_batch_id else None
        )
        print(json.dumps(counts, ensure_ascii=False, indent=2))
        failures = []
        if counts["funds"] != 2479:
            failures.append(f"funds expected 2479, got {counts['funds']}")
        if counts["report_sources"] != 853:
            failures.append(
                f"report sources expected 853, got {counts['report_sources']}"
            )
        if counts["batches"] < 1:
            failures.append("no dataset batch")
        if not counts["current_batch_id"]:
            failures.append("publication pointer is empty")
        if failures:
            print("[FAILED] " + "; ".join(failures))
            return 2
    print("[OK] D1 verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

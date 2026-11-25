#!/usr/bin/env python
"""Classify the 775 crawler source rows into a prioritized remediation queue."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import create_engine, text


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    """解析缺口分类参数；参数为空。"""

    parser = argparse.ArgumentParser(description="Build the source coverage gap queue.")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("PLATFORM_DATABASE_URL", ""),
        help="数据库连接地址；默认读取 PLATFORM_DATABASE_URL。",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=(
            PROJECT_ROOT
            / "docs"
            / "development"
            / "artifacts"
            / "20261027-day2"
            / "source-gap-queue.json"
        ),
        help="缺口分类 JSON 输出路径。",
    )
    return parser.parse_args()


def classify(row: dict[str, Any]) -> str:
    """返回单条来源的缺口类型；参数 row 为来源行。"""

    has_url = bool(str(row.get("monthly_report_url") or "").strip())
    has_command = bool(str(row.get("run_command") or "").strip())
    if not has_url and not has_command:
        return "missing_url_and_command"
    if not has_url:
        return "missing_url"
    if not has_command:
        return "missing_command"
    return "complete"


def main() -> int:
    """生成来源缺口清单并按管理人优先级排序；参数为空。"""

    args = parse_args()
    if not args.database_url:
        raise SystemExit("PLATFORM_DATABASE_URL or --database-url is required")
    engine = create_engine(args.database_url, pool_pre_ping=True)
    with engine.connect() as connection:
        rows = [
            dict(item)
            for item in connection.execute(
                text(
                    """
                    SELECT s.id, s.fund_id, f.fund_code, f.name_cn,
                           s.manager_name_source, s.fund_name_source,
                           s.monthly_report_url, s.fallback_report_url,
                           s.run_command, s.match_status, s.is_active
                    FROM fund_report_sources s
                    LEFT JOIN funds f ON f.id = s.fund_id
                    ORDER BY s.manager_name_source, f.fund_code
                    """
                )
            ).mappings()
        ]
    engine.dispose()
    items = []
    for row in rows:
        reason = classify(row)
        items.append(
            {
                "source_id": str(row["id"]),
                "fund_id": str(row["fund_id"]) if row["fund_id"] else None,
                "fund_code": row["fund_code"],
                "fund_name": row["name_cn"] or row["fund_name_source"],
                "manager": row["manager_name_source"],
                "reason": reason,
                "is_active": bool(row["is_active"]),
                "action": (
                    "run_company_probe_and_backfill"
                    if reason != "complete"
                    else "none"
                ),
            }
        )
    gap_items = [item for item in items if item["reason"] != "complete"]
    manager_counts = Counter(item["manager"] for item in gap_items)
    reason_counts = Counter(item["reason"] for item in gap_items)
    managers = [
        {
            "manager": manager,
            "gap_count": count,
            "priority": index,
        }
        for index, (manager, count) in enumerate(
            manager_counts.most_common(),
            start=1,
        )
    ]
    report = {
        "schema_version": "source-gap-queue-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_sources": len(items),
        "complete_sources": len(items) - len(gap_items),
        "gap_sources": len(gap_items),
        "reason_counts": dict(reason_counts),
        "manager_priorities": managers,
        "items": items,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "total_sources": report["total_sources"],
                "complete_sources": report["complete_sources"],
                "gap_sources": report["gap_sources"],
                "reason_counts": report["reason_counts"],
                "top_managers": managers[:10],
                "report": str(args.report),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

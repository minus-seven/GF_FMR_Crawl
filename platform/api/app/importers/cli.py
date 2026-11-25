"""Command-line entry point for the D1 repeatable importer."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

from app.core.config import DEFAULT_DATABASE_URL, get_settings
from app.db.session import create_session_factory, session_scope
from app.importers.service import D1ImportService


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析导入参数；参数 argv：可选命令行参数列表。"""

    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="把 爬虫list.xlsx 和最新结构化批次导入 D1 PostgreSQL 数据库。"
    )
    parser.add_argument(
        "--database-url",
        default=settings.database_url or DEFAULT_DATABASE_URL,
        help="SQLAlchemy PostgreSQL 连接地址。",
    )
    parser.add_argument(
        "--hkex-workbook",
        type=Path,
        default=settings.hkex_workbook,
        help="HKEX 权威基金主数据工作簿路径。",
    )
    parser.add_argument(
        "--crawler-workbook",
        type=Path,
        default=settings.crawler_workbook,
        help="承载月报链接和运行方式的爬虫工作簿路径。",
    )
    parser.add_argument(
        "--structured-root",
        type=Path,
        help="结构化批次目录；默认选择 data 下最新可用批次。",
    )
    parser.add_argument(
        "--no-activate",
        action="store_true",
        help="导入后不切换当前发布快照。",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="导入报告路径；默认写入结构化批次的 reports 目录。",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """执行 D1 导入；参数 argv：可选命令行参数列表。"""

    args = parse_args(argv)
    settings = get_settings()
    factory = create_session_factory(args.database_url)
    try:
        with session_scope(factory) as session:
            service = D1ImportService(
                session,
                project_root=settings.project_root,
                hkex_workbook_path=args.hkex_workbook,
                crawler_workbook_path=args.crawler_workbook,
            )
            summary = service.import_all(
                structured_root=args.structured_root,
                activate=not args.no_activate,
            )
    except Exception as exc:
        print(f"[FAILED] D1 导入失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    payload = summary.to_dict()
    report_path = args.report
    if report_path is None:
        structured_root = (
            args.structured_root.resolve()
            if args.structured_root
            else settings.data_root
        )
        if not (structured_root / "structure_data").exists():
            from app.importers.service import find_latest_structured_batch

            structured_root = find_latest_structured_batch(settings.data_root)
        report_path = (
            structured_root
            / "reports"
            / f"d1_import_report_{datetime.now():%Y%m%d_%H%M%S}.json"
        )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        "[OK] D1 import "
        f"batch={payload['batch_id']} "
        f"funds={payload['funds']} "
        f"metrics={payload['metrics']} "
        f"holdings={payload['holdings']} "
        f"allocations={payload['allocations']} "
        f"quality_issues={payload['quality_issues']} "
        f"no_op={payload['no_op']}"
    )
    print(f"[OK] report={report_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Import Bloomberg summary returns/volatility into the current published batch.

Only ``case/summary.csv`` is written to the database.  ``daily_points.csv`` is
intentionally not imported; it remains a local evidence file referenced by the
source metadata.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import create_session_factory, session_scope
from app.importers.common import relative_path, stable_uuid, utc_now
from app.models import (
    DatasetBatch,
    Document,
    DocumentSubject,
    DocumentVersion,
    Fund,
    FundMetric,
    FundReportSource,
    PublicationState,
)


METRICS = (
    ("annualized_return_percent", "annualized_return_percent"),
    ("annualized_volatility_percent", "annualized_volatility_percent"),
)


def _float(value: str, field: str, code: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{code} 的 {field} 不是数字：{value!r}") from exc


def read_summary(path: Path) -> list[dict[str, Any]]:
    """Read and validate the Bloomberg summary CSV."""

    if not path.is_file():
        raise FileNotFoundError(f"summary.csv 不存在：{path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("summary.csv 没有数据行")
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        code = str(row.get("code") or "").strip()
        if not code:
            raise ValueError("summary.csv 存在空基金代码")
        if code in seen:
            raise ValueError(f"summary.csv 存在重复基金代码：{code}")
        seen.add(code)
        if str(row.get("status") or "").strip().lower() != "ok":
            raise ValueError(f"{code} 的 Bloomberg 汇总状态不是 ok：{row.get('status')}")
        start = str(row.get("requested_start") or "").strip()
        end = str(row.get("requested_end") or "").strip()
        try:
            end_date = date.fromisoformat(end)
        except ValueError as exc:
            raise ValueError(f"{code} 的 requested_end 无效：{end!r}") from exc
        result.append(
            {
                "code": code,
                "rank": int(row.get("rank") or 0),
                "name": str(row.get("name") or "").strip(),
                "ticker": str(row.get("bbg_ticker") or "").strip(),
                "share_class": str(row.get("share_class") or "").strip(),
                "start": start,
                "end": end,
                "end_date": end_date,
                "observations": int(row.get("observations") or 0),
                "first_observation": str(row.get("first_observation") or "").strip(),
                "last_observation": str(row.get("last_observation") or "").strip(),
                "series_used": str(row.get("series_used") or "").strip(),
                "return_type": str(row.get("return_type") or "").strip(),
                "annualized_return_percent": _float(
                    row.get("annualized_return_percent"),
                    "annualized_return_percent",
                    code,
                ),
                "annualized_volatility_percent": _float(
                    row.get("annualized_volatility_percent"),
                    "annualized_volatility_percent",
                    code,
                ),
            }
        )
    return result


def source_digest(summary_path: Path) -> str:
    """Hash the imported summary file; daily points are deliberately excluded."""

    return hashlib.sha256(summary_path.read_bytes()).hexdigest()


def upsert_metric(
    session,
    *,
    batch: DatasetBatch,
    fund: Fund,
    version: DocumentVersion,
    report_period: date,
    metric_name: str,
    value: float,
    metadata: dict[str, Any],
) -> None:
    """Idempotently upsert one Bloomberg metric."""

    metric = session.scalar(
        select(FundMetric).where(
            FundMetric.batch_id == batch.id,
            FundMetric.fund_id == fund.id,
            FundMetric.document_version_id == version.id,
            FundMetric.report_period == report_period,
            FundMetric.metric_name == metric_name,
        )
    )
    if metric is None:
        metric = FundMetric(
            id=stable_uuid(
                "fund-metric",
                batch.id,
                fund.id,
                version.id,
                report_period,
                metric_name,
            ),
            fund_id=fund.id,
            batch_id=batch.id,
            document_version_id=version.id,
            report_period=report_period,
            metric_name=metric_name,
        )
        session.add(metric)
    metric.numeric_value = value
    metric.text_value = None
    metric.unit = "percent"
    metric.confidence = "bloomberg"
    metric.review_status = "approved"
    metric.source_page = None
    metric.metadata_json = metadata


def import_summary(summary_path: Path, database_url: str | None = None) -> dict[str, Any]:
    """Import the summary into the current published batch."""

    settings = get_settings()
    project_root = settings.project_root
    rows = read_summary(summary_path)
    digest = source_digest(summary_path)
    state_factory = create_session_factory(database_url or settings.database_url)
    with session_scope(state_factory) as session:
        state = session.get(PublicationState, 1)
        if state is None or state.current_batch_id is None:
            raise RuntimeError("当前没有已发布数据快照，不能导入 Bloomberg 指标")
        batch = session.get(DatasetBatch, state.current_batch_id)
        if batch is None or batch.status != "published":
            raise RuntimeError("当前发布指针不是有效的 published 批次")

        codes = [row["code"] for row in rows]
        funds = {
            fund.fund_code: fund
            for fund in session.scalars(select(Fund).where(Fund.fund_code.in_(codes)))
        }
        missing = sorted(set(codes) - set(funds))
        if missing:
            raise RuntimeError("数据库不存在基金代码：" + ", ".join(missing))

        source_id = f"bloomberg-market-stats:{digest}"
        document = session.scalar(select(Document).where(Document.source_id == source_id))
        if document is None:
            document = Document(
                id=stable_uuid("document", source_id),
                document_type="bloomberg_historical_market_data",
                source_id=source_id,
                first_seen_at=utc_now(),
            )
            session.add(document)
            session.flush()

        version = session.scalar(
            select(DocumentVersion).where(DocumentVersion.sha256 == digest)
        )
        if version is None:
            version = DocumentVersion(
                id=stable_uuid("document-version", document.id, digest),
                document_id=document.id,
                batch_id=batch.id,
                source_url=None,
                report_date=rows[0]["end_date"],
                sha256=digest,
                mime_type="text/csv",
                size_bytes=summary_path.stat().st_size,
                file_path=relative_path(summary_path, project_root),
            )
            session.add(version)
            session.flush()
        elif version.report_date is None:
            version.report_date = rows[0]["end_date"]

        summary_relative = relative_path(summary_path, project_root)
        daily_path = summary_path.with_name("daily_points.csv")
        daily_relative = relative_path(daily_path, project_root) if daily_path.exists() else None
        imported = 0
        label = f"Bloomberg日点位 · {rows[0]['start']}至{rows[0]['end']}"
        for row in rows:
            fund = funds[row["code"]]
            subject = session.scalar(
                select(DocumentSubject).where(
                    DocumentSubject.document_version_id == version.id,
                    DocumentSubject.fund_id == fund.id,
                )
            )
            if subject is None:
                session.add(
                    DocumentSubject(
                        id=stable_uuid("document-subject", version.id, fund.id),
                        document_version_id=version.id,
                        fund_id=fund.id,
                        subject_name=fund.name_cn,
                        start_page=None,
                        end_page=None,
                        match_method="fund_code",
                        match_score=1,
                    )
                )

            metadata = {
                "source": "Bloomberg Desktop API",
                "bbg_ticker": row["ticker"],
                "share_class": row["share_class"],
                "requested_start": row["start"],
                "requested_end": row["end"],
                "observations": row["observations"],
                "first_observation": row["first_observation"],
                "last_observation": row["last_observation"],
                "series_used": row["series_used"],
                "return_type": row["return_type"],
                "summary_path": summary_relative,
                "daily_points_path": daily_relative,
                "document_version_id": str(version.id),
            }
            for column, metric_name in METRICS:
                upsert_metric(
                    session,
                    batch=batch,
                    fund=fund,
                    version=version,
                    report_period=row["end_date"],
                    metric_name=metric_name,
                    value=row[column],
                    metadata=metadata,
                )
                imported += 1

            source_key = f"bloomberg-market-stats:{digest}:{row['code']}"
            report_source = session.scalar(
                select(FundReportSource).where(FundReportSource.source_key == source_key)
            )
            source_metadata = {
                "document_version_id": str(version.id),
                "source_label": label,
                "report_kind": "market_data",
                "report_period": row["end"],
                "language": "en",
                "available_sections": ["日点位", "年化收益率", "年化波动率"],
                "summary_path": summary_relative,
                "daily_points_path": daily_relative,
            }
            if report_source is None:
                session.add(
                    FundReportSource(
                        id=stable_uuid("fund-report-source", source_key),
                        fund_id=fund.id,
                        source_system="bloomberg",
                        source_key=source_key,
                        source_sheet="case/summary.csv",
                        source_row=int(row["rank"]),
                        fund_name_source=row["name"] or fund.name_cn,
                        match_status="matched",
                        match_method="fund_code",
                        match_confidence="high",
                        is_active=True,
                        metadata_json=source_metadata,
                    )
                )
            else:
                report_source.metadata_json = source_metadata
                report_source.is_active = True

        return {
            "batch_id": str(batch.id),
            "summary_path": summary_relative,
            "daily_points_stored": False,
            "source_sha256": digest,
            "funds": len(rows),
            "metrics": imported,
            "document_version_id": str(version.id),
        }


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary",
        type=Path,
        default=settings.project_root / "case" / "summary.csv",
    )
    parser.add_argument("--database-url", default=settings.database_url)
    args = parser.parse_args(argv)
    try:
        result = import_summary(args.summary.resolve(), args.database_url)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAILED] Bloomberg 汇总导入失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print("[OK] Bloomberg 汇总已落库 " + json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

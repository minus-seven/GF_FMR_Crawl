"""Review workflow for structured-data quality issues."""

from __future__ import annotations

from datetime import date
import json
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.importers.common import stable_uuid, utc_now
from app.models import (
    DatasetBatch,
    DocumentVersion,
    Fund,
    FundAllocation,
    FundHolding,
    FundMetric,
    QualityIssue,
)
from app.schemas.funds import (
    FundDetailData,
    QualityIssueData,
    QualityReviewActionData,
    QualityReviewData,
    QualityReviewUpdate,
)
from app.services.queries import get_current_snapshot, get_fund_detail


def _issue(session: Session, issue_id: UUID) -> tuple[QualityIssue, DatasetBatch, Fund | None]:
    current = get_current_snapshot(session)
    item = session.scalar(
        select(QualityIssue).where(
            QualityIssue.id == issue_id,
            QualityIssue.batch_id == current.id,
        )
    )
    if item is None:
        raise ApiError(404, "E_QUALITY_ISSUE_NOT_FOUND", "质量问题不存在或不属于当前快照")
    fund = session.get(Fund, item.fund_id) if item.fund_id else None
    return item, current.batch, fund


def _issue_data(
    session: Session,
    item: QualityIssue,
    detail: FundDetailData | None = None,
) -> QualityIssueData:
    fund = session.get(Fund, item.fund_id) if item.fund_id else None
    return QualityIssueData(
        issue_id=item.id,
        fund_id=item.fund_id,
        fund_code=fund.fund_code if fund else None,
        fund_name=fund.name_cn if fund else None,
        severity=item.severity,
        issue_type=item.issue_type,
        details=item.details,
        status=item.status,
        created_at=item.created_at,
        metadata=item.metadata_json or {},
        manager_name=detail.manager_name if detail else None,
        asset_category=detail.asset_category if detail else None,
        asset_subcategory=detail.asset_subcategory if detail else None,
        base_currency=detail.base_currency if detail else None,
        domicile=detail.domicile if detail else None,
        fund_status=detail.status if detail else None,
        report_period=detail.report_period if detail else None,
    )


def _base_record(detail: FundDetailData | None) -> dict[str, Any]:
    if detail is None:
        return {}
    return detail.model_dump(mode="json")


def get_review(session: Session, issue_id: UUID) -> tuple[QualityReviewData, UUID]:
    """Return one issue, complete fund context and editable JSON record."""

    item, _batch, fund = _issue(session, issue_id)
    detail = None
    if fund is not None:
        detail, snapshot_id = get_fund_detail(session, fund.fund_code)
    else:
        snapshot_id = get_current_snapshot(session).id
    metadata = item.metadata_json or {}
    review = metadata.get("review") if isinstance(metadata, dict) else None
    review = review if isinstance(review, dict) else {}
    record = review.get("record") if isinstance(review.get("record"), dict) else _base_record(detail)
    audit = {key: value for key, value in review.items() if key != "record"}
    return QualityReviewData(
        issue=_issue_data(session, item, detail),
        fund=detail,
        record=record,
        audit=audit,
    ), snapshot_id


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ApiError(422, "E_QUALITY_RECORD", f"数值字段无法解析：{value!r}") from exc


def _as_date(value: Any, fallback: date | None) -> date | None:
    if value in (None, ""):
        return fallback
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ApiError(422, "E_QUALITY_RECORD", f"报告期无法解析：{value!r}") from exc


def _apply_record(
    session: Session,
    *,
    batch: DatasetBatch,
    fund: Fund,
    record: dict[str, Any],
    resolve: bool,
) -> list[str]:
    """Apply editable structured sections while leaving fund identity read-only."""

    if len(json.dumps(record, ensure_ascii=False)) > 1_000_000:
        raise ApiError(413, "E_QUALITY_RECORD_TOO_LARGE", "编辑内容超过 1 MB")
    detail, _ = get_fund_detail(session, fund.fund_code)
    report_period = _as_date(record.get("report_period"), detail.report_period)
    document_id = None
    latest = record.get("latest_document")
    if isinstance(latest, dict):
        document_id = latest.get("document_version_id")
    if not document_id and detail.latest_document:
        document_id = str(detail.latest_document.get("document_version_id"))
    document_version = session.get(DocumentVersion, document_id) if document_id else None
    if report_period is None or document_version is None:
        raise ApiError(422, "E_QUALITY_RECORD", "缺少可编辑记录对应的报告期或文档版本")

    changed: list[str] = []
    metrics = record.get("metrics")
    if isinstance(metrics, list):
        for value in metrics:
            if not isinstance(value, dict) or not str(value.get("metric_name") or "").strip():
                continue
            name = str(value["metric_name"]).strip()
            row = session.scalar(
                select(FundMetric).where(
                    FundMetric.batch_id == batch.id,
                    FundMetric.fund_id == fund.id,
                    FundMetric.document_version_id == document_version.id,
                    FundMetric.report_period == report_period,
                    FundMetric.metric_name == name,
                )
            )
            if row is None:
                row = FundMetric(
                    id=stable_uuid("fund-metric", batch.id, fund.id, document_version.id, report_period, name),
                    batch_id=batch.id,
                    fund_id=fund.id,
                    document_version_id=document_version.id,
                    report_period=report_period,
                    metric_name=name,
                )
                session.add(row)
            row.numeric_value = _as_float(value.get("numeric_value"))
            row.text_value = value.get("text_value")
            row.unit = value.get("unit")
            row.review_status = "approved" if resolve else "pending"
            row.confidence = "human_review"
            changed.append("metrics")

    holdings = record.get("holdings")
    if isinstance(holdings, list):
        session.execute(
            delete(FundHolding).where(
                FundHolding.batch_id == batch.id,
                FundHolding.fund_id == fund.id,
                FundHolding.document_version_id == document_version.id,
                FundHolding.report_period == report_period,
            )
        )
        for value in holdings:
            if not isinstance(value, dict) or not str(value.get("security_name") or "").strip():
                continue
            name = str(value["security_name"]).strip()
            ticker = str(value.get("ticker") or "").strip() or None
            session.add(FundHolding(
                id=stable_uuid("fund-holding", batch.id, fund.id, document_version.id, report_period, name, ticker),
                batch_id=batch.id,
                fund_id=fund.id,
                document_version_id=document_version.id,
                report_period=report_period,
                security_name=name,
                ticker=ticker,
                holding_quantity=_as_float(value.get("holding_quantity")),
                registration_place=str(value.get("registration_place") or "").strip() or None,
                holding_type=str(value.get("holding_type") or "").strip() or None,
                weight=_as_float(value.get("weight")),
                market_value=_as_float(value.get("market_value")),
                source_page=value.get("source_page"),
            ))
        changed.append("holdings")

    allocations = record.get("allocations")
    if isinstance(allocations, list):
        session.execute(
            delete(FundAllocation).where(
                FundAllocation.batch_id == batch.id,
                FundAllocation.fund_id == fund.id,
                FundAllocation.document_version_id == document_version.id,
                FundAllocation.report_period == report_period,
            )
        )
        for value in allocations:
            if not isinstance(value, dict) or not str(value.get("category") or "").strip():
                continue
            category = str(value["category"]).strip()
            dimension = str(value.get("dimension") or "other").strip()
            session.add(FundAllocation(
                id=stable_uuid("fund-allocation", batch.id, fund.id, document_version.id, report_period, dimension, category),
                batch_id=batch.id,
                fund_id=fund.id,
                document_version_id=document_version.id,
                report_period=report_period,
                dimension=dimension,
                category=category,
                weight=_as_float(value.get("weight")),
                unit=value.get("unit"),
                source_page=value.get("source_page"),
                metadata_json={"review_source": "quality_workbench"},
            ))
        changed.append("allocations")
    return sorted(set(changed))


def save_review(
    session: Session,
    issue_id: UUID,
    body: QualityReviewUpdate,
    *,
    action: str,
) -> tuple[QualityReviewActionData, UUID]:
    """Persist an audited edit or confirmation for the current snapshot."""

    item, batch, fund = _issue(session, issue_id)
    now = utc_now()
    changed = _apply_record(session, batch=batch, fund=fund, record=body.record, resolve=body.resolve) if fund else []
    metadata = dict(item.metadata_json or {})
    previous_review = metadata.get("review") if isinstance(metadata.get("review"), dict) else {}
    history = list(previous_review.get("history") or [])
    history.append({"action": action, "at": now.isoformat(), "comment": body.comment})
    review = {
        "status": "resolved" if body.resolve else "open",
        "reviewed_at": now.isoformat(),
        "reviewed_by": "web-user",
        "comment": body.comment,
        "record": body.record,
        "history": history[-20:],
    }
    metadata["review"] = review
    item.metadata_json = metadata
    item.status = "resolved" if body.resolve else "open"
    session.commit()
    return QualityReviewActionData(
        issue_id=item.id,
        status=item.status,
        updated_at=now,
        changed_sections=changed,
    ), batch.id


def confirm_review(session: Session, issue_id: UUID, comment: str | None) -> tuple[QualityReviewActionData, UUID]:
    """Resolve an issue without changing its structured values."""

    item, batch, _fund = _issue(session, issue_id)
    now = utc_now()
    metadata = dict(item.metadata_json or {})
    previous_review = metadata.get("review") if isinstance(metadata.get("review"), dict) else {}
    history = list(previous_review.get("history") or [])
    history.append({"action": "confirm", "at": now.isoformat(), "comment": comment})
    metadata["review"] = {
        **previous_review,
        "status": "resolved",
        "reviewed_at": now.isoformat(),
        "reviewed_by": "web-user",
        "comment": comment,
        "history": history[-20:],
    }
    item.metadata_json = metadata
    item.status = "resolved"
    session.commit()
    return QualityReviewActionData(
        issue_id=item.id,
        status=item.status,
        updated_at=now,
        changed_sections=[],
    ), batch.id

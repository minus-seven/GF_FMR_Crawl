"""Read-only deterministic queries used by the public API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable
from uuid import UUID

from sqlalchemy import and_, case, cast, exists, func, or_, select, String, text
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.models import (
    DatasetBatch,
    Document,
    DocumentSubject,
    DocumentVersion,
    Fund,
    FundAllocation,
    FundClassification,
    FundHolding,
    FundListing,
    FundMetric,
    FundRegion,
    FundReportSource,
    FundScaleHistory,
    FundSourceId,
    FundTag,
    Manager,
    PublicationState,
    QualityIssue,
    ShareClass,
    Umbrella,
)
from app.schemas.analytics import PerspectiveData, PerspectiveView
from app.schemas.common import QualityStatus
from app.schemas.funds import (
    AllocationValue,
    FundDetailData,
    FundReportData,
    FundPageData,
    FundSummary,
    HoldingValue,
    MetricListData,
    MetricValue,
    QualityIssueData,
    QualityPageData,
    ReportSource,
)
from app.schemas.snapshots import SnapshotData
from app.services.download_sources import sources_for_source
from app.services.allocation_labels import merge_allocation_rows
from app.services.report_type import (
    REPORT_KIND_ANNUAL,
    REPORT_KIND_MONTHLY,
    REPORT_KIND_SEMIANNUAL,
    report_kind_label,
)


@dataclass(frozen=True)
class CurrentSnapshot:
    """Current snapshot database state and fund count."""

    state: PublicationState
    batch: DatasetBatch
    fund_count: int

    @property
    def id(self) -> UUID:
        """返回当前批次 UUID。"""

        return self.batch.id


def _month_end_clause(column):
    """限制规模口径为自然月末，避免把事件/持仓日期画进月度趋势。"""

    last_day = func.date_trunc("month", column) + text("interval '1 month - 1 day'")
    return func.date_part("day", column) == func.date_part("day", last_day)


def get_current_snapshot(session: Session) -> CurrentSnapshot:
    """读取唯一当前发布快照；参数 session：数据库 Session。"""

    state = session.get(PublicationState, 1)
    if state is None or state.current_batch_id is None:
        raise ApiError(
            503,
            "E_SNAPSHOT_UNAVAILABLE",
            "当前没有已发布数据快照",
            retryable=True,
        )
    batch = session.get(DatasetBatch, state.current_batch_id)
    if batch is None or batch.status != "published":
        raise ApiError(
            503,
            "E_SNAPSHOT_INVALID",
            "当前发布指针未指向有效批次",
            retryable=True,
        )
    fund_count = int(session.scalar(select(func.count()).select_from(Fund)) or 0)
    return CurrentSnapshot(state=state, batch=batch, fund_count=fund_count)


def snapshot_data(session: Session) -> SnapshotData:
    """返回当前快照响应数据；参数 session：数据库 Session。"""

    current = get_current_snapshot(session)
    return SnapshotData(
        snapshot_id=current.id,
        status=current.batch.status,
        source_kind=current.batch.source_kind,
        source_sha256=current.batch.source_sha256,
        coverage=(
            float(current.batch.coverage)
            if current.batch.coverage is not None
            else None
        ),
        fund_count=current.fund_count,
        published_at=_to_utc(current.batch.published_at),
        release_version=current.state.release_version,
    )


def _quality_from_issues(
    issues: Iterable[QualityIssue],
    *,
    has_metrics: bool,
    has_document: bool,
) -> QualityStatus:
    """根据问题严重度和数据存在性计算质量状态；参数为问题与数据标志。"""

    issues = list(issues)
    if any(issue.severity in {"error", "blocker"} for issue in issues):
        return QualityStatus.REVIEW_REQUIRED
    if issues:
        return QualityStatus.VALIDATION_WARNING
    if not has_document:
        return QualityStatus.MISSING_FILE
    if not has_metrics:
        return QualityStatus.MISSING_METRIC
    return QualityStatus.COMPLETE


def _to_utc(value: datetime | None) -> datetime | None:
    """把数据库时间转换为 UTC；参数 value：带时区时间。"""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def list_funds(
    session: Session,
    *,
    manager_id: UUID | None,
    manager: str | None,
    category: str | None,
    report_period: date | None,
    status: str | None,
    query: str | None,
    secondary_filters: list[str] | None = None,
    sort_by: str | None = None,
    sort_direction: str | None = None,
    page: int,
    page_size: int,
) -> tuple[FundPageData, UUID]:
    """查询基金分页；参数为过滤条件、分页和 Session。"""

    current = get_current_snapshot(session)
    latest_period = (
        select(
            FundMetric.fund_id.label("fund_id"),
            func.max(FundMetric.report_period).label("report_period"),
        )
        .where(FundMetric.batch_id == current.id)
        .group_by(FundMetric.fund_id)
        .subquery()
    )
    issue_summary = (
        select(
            QualityIssue.fund_id.label("fund_id"),
            func.max(
                case((QualityIssue.severity.in_(["error", "blocker"]), 1), else_=0)
            ).label("has_error"),
            func.max(
                case((QualityIssue.severity == "warning", 1), else_=0)
            ).label("has_warning"),
        )
        .where(QualityIssue.batch_id == current.id)
        .group_by(QualityIssue.fund_id)
        .subquery()
    )
    scale_date = (
        select(func.max(FundScaleHistory.as_of_date))
        .where(
            FundScaleHistory.fund_id == Fund.id,
            FundScaleHistory.source_batch_id == current.id,
            FundScaleHistory.as_of_date <= date.today(),
            _month_end_clause(FundScaleHistory.as_of_date),
        )
        .correlate(Fund)
        .scalar_subquery()
    )
    scale_value = (
        select(FundScaleHistory.fund_size)
        .where(
            FundScaleHistory.fund_id == Fund.id,
            FundScaleHistory.source_batch_id == current.id,
            FundScaleHistory.as_of_date == scale_date,
        )
        .order_by(FundScaleHistory.id.desc())
        .limit(1)
        .correlate(Fund)
        .scalar_subquery()
    )
    base = (
        select(
            Fund,
            Manager.name_cn.label("manager_name"),
            Umbrella.name_cn.label("umbrella_name"),
            FundClassification.level_1.label("asset_category"),
            FundClassification.level_2.label("asset_subcategory"),
            latest_period.c.report_period,
            issue_summary.c.has_error,
            issue_summary.c.has_warning,
            scale_value.label("latest_scale"),
            scale_date.label("latest_scale_date"),
        )
        .join(Manager, Manager.id == Fund.manager_id)
        .outerjoin(Umbrella, Umbrella.id == Fund.umbrella_id)
        .outerjoin(FundClassification, FundClassification.fund_id == Fund.id)
        .outerjoin(latest_period, latest_period.c.fund_id == Fund.id)
        .outerjoin(issue_summary, issue_summary.c.fund_id == Fund.id)
    )
    source_raw = (
        select(
            FundSourceId.fund_id.label("fund_id"),
            FundSourceId.metadata_json.label("metadata_json"),
        )
        .where(FundSourceId.source_system.in_(["HKEX", "hkex-master"]))
        .order_by(FundSourceId.fund_id, FundSourceId.created_at.desc())
        .distinct(FundSourceId.fund_id)
        .subquery()
    )
    base = base.outerjoin(source_raw, source_raw.c.fund_id == Fund.id)
    conditions = []
    if manager_id:
        conditions.append(Fund.manager_id == manager_id)
    if manager:
        pattern = f"%{manager}%"
        conditions.append(
            or_(Manager.name_cn.ilike(pattern), Manager.short_name.ilike(pattern))
        )
    if category:
        conditions.append(FundClassification.level_1 == category)
    if status:
        conditions.append(Fund.status == status)
    if query:
        pattern = f"%{query}%"
        conditions.append(
            or_(
                Fund.fund_code.ilike(pattern),
                Fund.name_cn.ilike(pattern),
                Fund.name_en.ilike(pattern),
            )
        )
    if report_period:
        conditions.append(
            exists(
                select(FundMetric.id).where(
                    FundMetric.fund_id == Fund.id,
                    FundMetric.batch_id == current.id,
                    FundMetric.report_period == report_period,
                )
            )
        )
    raw_row = source_raw.c.metadata_json["raw_row"]
    currency_source_s = raw_row["基础货币"].astext
    currency_source_ac = raw_row["资产类别二级分类"].astext
    currency_expression = case(
        (
            currency_source_ac.in_(["港元，美元", "港元，美元，人民币"]),
            currency_source_ac,
        ),
        else_=currency_source_s,
    )
    secondary_map = {
        "ownership": raw_row["管理人中外资"].astext,
        "management": Fund.management_style,
        "listing": Fund.listing_status,
        "leverage": Fund.leverage_inverse_status,
        "currency": currency_expression,
        "northbound": raw_row["北上标签"].astext,
        "connect": raw_row["ETF互联互通标签"].astext,
        "sixty_forty": Fund.sixty_forty_classification,
    }
    if secondary_filters:
        grouped: dict[str, list[str]] = {}
        for item in secondary_filters:
            if ":" not in item:
                continue
            key, value = item.split(":", 1)
            if key in secondary_map and value:
                grouped.setdefault(key, []).append(value)
        for key, values in grouped.items():
            expression = secondary_map[key]
            normalized_values = set(values)
            if key == "currency":
                # 单币种筛选严格按 S 列“基础货币”；多币种筛选单独按 AC 列的组合值。
                currency_clauses = []
                single_currency_values = normalized_values.intersection({"美元", "港元", "人民币"})
                if single_currency_values:
                    currency_clauses.append(currency_source_s.in_(sorted(single_currency_values)))
                if "多币种" in normalized_values:
                    currency_clauses.append(
                        currency_source_ac.in_(["港元，美元", "港元，美元，人民币"])
                    )
                if currency_clauses:
                    conditions.append(or_(*currency_clauses))
                continue
            if key == "leverage":
                normalized_values |= {"非杠反", "不适用"} if "非杠反" in normalized_values else set()
            if key == "connect" and "互联互通" in normalized_values:
                normalized_values.add("是")
            if key == "northbound" and "非北上" in normalized_values:
                normalized_values.update({"不适用", "否"})
            if key == "sixty_forty" and "不适用" in normalized_values:
                normalized_values.add("否")
            conditions.append(expression.in_(sorted(normalized_values)))
    if conditions:
        base = base.where(*conditions)

    filtered_ids = base.with_only_columns(Fund.id).distinct().subquery()
    total = int(session.scalar(select(func.count()).select_from(filtered_ids)) or 0)
    matching_codes = list(
        session.scalars(
            select(Fund.fund_code)
            .where(Fund.id.in_(select(filtered_ids.c.id)))
            .order_by(Fund.fund_code)
        )
    )

    order_clause = Fund.fund_code.asc()
    if sort_by == "latest_scale":
        order_clause = scale_value.desc().nullslast() if sort_direction != "asc" else scale_value.asc().nullsfirst()
        rows_statement = base.order_by(order_clause, Fund.fund_code.asc())
    else:
        rows_statement = base.order_by(order_clause)
    rows = session.execute(
        rows_statement
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = []
    for row in rows:
        fund = row.Fund
        if row.has_error:
            quality = QualityStatus.REVIEW_REQUIRED
        elif row.has_warning:
            quality = QualityStatus.VALIDATION_WARNING
        elif row.report_period is None:
            quality = QualityStatus.MISSING_METRIC
        else:
            quality = QualityStatus.COMPLETE
        source_id = session.scalar(select(FundSourceId).where(FundSourceId.fund_id == fund.id).limit(1))
        raw_values = (source_id.metadata_json or {}).get("raw_row", {}) if source_id else {}
        items.append(
            FundSummary(
                fund_id=fund.id,
                fund_code=fund.fund_code,
                name_cn=fund.name_cn,
                name_en=fund.name_en,
                manager_id=fund.manager_id,
                manager_name=row.manager_name,
                umbrella_name=row.umbrella_name,
                asset_category=row.asset_category,
                asset_subcategory=row.asset_subcategory,
                status=fund.status,
                report_period=row.report_period,
                quality_status=quality,
                latest_scale=float(row.latest_scale) if row.latest_scale is not None else None,
                latest_scale_date=row.latest_scale_date,
                secondary_values={
                    "ownership": raw_values.get("管理人中外资"),
                    "management": fund.management_style,
                    "listing": fund.listing_status,
                    "leverage": fund.leverage_inverse_status,
                    "currency": (
                        raw_values.get("资产类别二级分类")
                        if raw_values.get("资产类别二级分类") in {"港元，美元", "港元，美元，人民币"}
                        else raw_values.get("基础货币")
                    ),
                    "northbound": raw_values.get("北上标签"),
                    "connect": raw_values.get("ETF互联互通标签"),
                    "sixty_forty": fund.sixty_forty_classification,
                },
            )
        )
    return (
        FundPageData(
            items=items,
            page=page,
            page_size=page_size,
            total=total,
            matching_fund_codes=matching_codes,
            secondary_options=secondary_filter_options(),
        ),
        current.id,
    )


def secondary_filter_options() -> list[dict[str, object]]:
    """返回基金库二级类别筛选维度；参数为空。"""

    return [
        {"key": "ownership", "label": "资本属性", "options": ["中资", "外资", "合资"]},
        {"key": "management", "label": "管理方式", "options": ["主动", "被动"]},
        {"key": "listing", "label": "上市状态", "options": ["上市", "非上市"]},
        {"key": "leverage", "label": "杠反", "options": ["杠反", "非杠反"]},
        {"key": "currency", "label": "币种", "options": ["美元", "港元", "人民币", "多币种"]},
        {"key": "northbound", "label": "北上状态", "options": ["已北上", "审批中", "非北上"]},
        {"key": "connect", "label": "互联互通", "options": ["互联互通", "指数符合", "不适用"]},
        {"key": "sixty_forty", "label": "60-40", "options": ["60-40", "不适用"]},
    ]


def _fund_base(session: Session, fund_code: str):
    """查询基金基础详情行；参数 session/fund_code 为查询上下文。"""

    row = session.execute(
        select(
            Fund,
            Manager,
            Umbrella,
            FundClassification,
        )
        .join(Manager, Manager.id == Fund.manager_id)
        .outerjoin(Umbrella, Umbrella.id == Fund.umbrella_id)
        .outerjoin(FundClassification, FundClassification.fund_id == Fund.id)
        .where(Fund.fund_code == fund_code)
    ).first()
    if row is None:
        raise ApiError(404, "E_FUND_NOT_FOUND", "基金不存在")
    return row


def _metric_value(metric: FundMetric, source_label: str | None = None) -> MetricValue:
    """转换指标数据库行；参数 metric：指标 ORM 记录。"""

    quality = (
        QualityStatus.REVIEW_REQUIRED
        if metric.review_status == "rejected"
        else QualityStatus.VALIDATION_WARNING
        if metric.confidence == "review"
        else QualityStatus.COMPLETE
    )
    return MetricValue(
        metric_name=metric.metric_name,
        numeric_value=(
            float(metric.numeric_value) if metric.numeric_value is not None else None
        ),
        text_value=metric.text_value,
        unit=metric.unit,
        report_period=metric.report_period,
        review_status=metric.review_status,
        quality_status=quality,
        source_page=metric.source_page,
        document_version_id=metric.document_version_id,
        source_label=source_label,
    )


def _document_source_label(
    version: DocumentVersion | None,
    report_sources: list[FundReportSource],
    document_type: str | None = None,
) -> str | None:
    """Return the human-facing source label for a document version."""

    if version is None:
        return None
    source_url = str(version.source_url or "")
    for source in report_sources:
        metadata = source.metadata_json if isinstance(source.metadata_json, dict) else {}
        if str(metadata.get("document_version_id") or "") == str(version.id):
            report_kind = str(metadata.get("report_kind") or "").strip()
            if report_kind in {REPORT_KIND_MONTHLY, REPORT_KIND_SEMIANNUAL, REPORT_KIND_ANNUAL}:
                return report_kind_label(report_kind, version.report_date)
            label = metadata.get("source_label")
            if label:
                return str(label)
        # URL 不是版本身份：同一 PDF 地址可能先后返回不同报告内容。
        # 只有明确绑定 document_version_id 的来源才能覆盖版本标签，
        # 否则旧快照会被新半年报误标成“半年报”。
    if version.report_date:
        kind = str(document_type or "").strip().casefold()
        if kind in {REPORT_KIND_MONTHLY, REPORT_KIND_SEMIANNUAL, REPORT_KIND_ANNUAL}:
            return report_kind_label(kind, version.report_date)
        # Historical rows may not have document_type populated.  Keep the
        # legacy date heuristic only as a last resort, while ensuring the
        # visible label still follows the canonical naming contract.
        if version.report_date.month == 6:
            return report_kind_label(REPORT_KIND_SEMIANNUAL, version.report_date)
        if version.report_date.month == 12:
            return report_kind_label(REPORT_KIND_ANNUAL, version.report_date)
        return report_kind_label(REPORT_KIND_MONTHLY, version.report_date)
    return None


def _document_source_metadata(
    version_id: UUID,
    report_sources: list[FundReportSource],
) -> dict:
    """Return supplementary metadata attached to one document version."""

    for source in report_sources:
        metadata = source.metadata_json if isinstance(source.metadata_json, dict) else {}
        if str(metadata.get("document_version_id") or "") == str(version_id):
            return metadata
    return {}


def get_fund_detail(
    session: Session,
    fund_code: str,
) -> tuple[FundDetailData, UUID]:
    """读取基金详情；参数 session/fund_code 为查询上下文。"""

    current = get_current_snapshot(session)
    row = _fund_base(session, fund_code)
    fund: Fund = row.Fund
    manager: Manager = row.Manager
    umbrella: Umbrella | None = row.Umbrella
    classification: FundClassification | None = row.FundClassification
    report_sources = list(
        session.scalars(
            select(FundReportSource)
            .where(
                FundReportSource.fund_id == fund.id,
                FundReportSource.is_active.is_(True),
            )
            .order_by(
                FundReportSource.priority,
                FundReportSource.source_sheet,
                FundReportSource.source_row,
            )
        )
    )
    hidden_version_ids: set[UUID] = set()
    for item in report_sources:
        if item.is_visible:
            continue
        raw_version_id = (item.metadata_json or {}).get("document_version_id")
        if not raw_version_id:
            continue
        try:
            hidden_version_ids.add(UUID(str(raw_version_id)))
        except (ValueError, TypeError):
            continue
    report_sources = [item for item in report_sources if item.is_visible]
    latest_period = session.scalar(
        select(func.max(FundMetric.report_period)).where(
            FundMetric.fund_id == fund.id,
            FundMetric.batch_id == current.id,
            *([~FundMetric.document_version_id.in_(hidden_version_ids)] if hidden_version_ids else []),
        )
    )
    all_metrics = list(
        session.scalars(
            select(FundMetric)
            .where(FundMetric.fund_id == fund.id, FundMetric.batch_id == current.id)
            .where(*([~FundMetric.document_version_id.in_(hidden_version_ids)] if hidden_version_ids else []))
            .order_by(FundMetric.report_period.desc(), FundMetric.metric_name)
        )
    )
    all_holdings = list(
        session.scalars(
            select(FundHolding)
            .where(FundHolding.fund_id == fund.id, FundHolding.batch_id == current.id)
            .where(*([~FundHolding.document_version_id.in_(hidden_version_ids)] if hidden_version_ids else []))
            .order_by(FundHolding.report_period.desc(), FundHolding.weight.desc().nullslast())
        )
    )
    all_allocations = list(
        session.scalars(
            select(FundAllocation)
            .where(FundAllocation.fund_id == fund.id, FundAllocation.batch_id == current.id)
            .where(*([~FundAllocation.document_version_id.in_(hidden_version_ids)] if hidden_version_ids else []))
            .order_by(FundAllocation.report_period.desc(), FundAllocation.dimension, FundAllocation.category)
        )
    )
    metrics = [item for item in all_metrics if item.report_period == latest_period]
    holdings = [item for item in all_holdings if item.report_period == latest_period]
    allocations = [item for item in all_allocations if item.report_period == latest_period]
    issues = list(
        session.scalars(
            select(QualityIssue).where(
                QualityIssue.fund_id == fund.id,
                QualityIssue.batch_id == current.id,
                QualityIssue.status == "open",
            )
        )
    )
    regions = list(
        session.scalars(
            select(FundRegion)
            .where(FundRegion.fund_id == fund.id)
            .order_by(FundRegion.region_level, FundRegion.region_name)
        )
    )
    tags = list(
        session.scalars(
            select(FundTag)
            .where(FundTag.fund_id == fund.id)
            .order_by(FundTag.tag_type, FundTag.tag_value)
        )
    )
    stock_codes = list(
        session.scalars(
            select(FundListing.stock_code)
            .join(ShareClass, ShareClass.id == FundListing.share_class_id)
            .where(ShareClass.fund_id == fund.id)
            .order_by(FundListing.stock_code)
        )
    )
    latest_document = session.execute(
        select(DocumentVersion, DocumentSubject)
        .join(DocumentSubject, DocumentSubject.document_version_id == DocumentVersion.id)
        .where(
            DocumentSubject.fund_id == fund.id,
            DocumentVersion.id.in_(
                select(FundMetric.document_version_id).where(
                    FundMetric.fund_id == fund.id,
                    FundMetric.batch_id == current.id,
                    *([~FundMetric.document_version_id.in_(hidden_version_ids)] if hidden_version_ids else []),
                )
            ),
        )
        .order_by(DocumentVersion.report_date.desc().nullslast())
        .limit(1)
    ).first()
    document_ids = {
        item.document_version_id
        for item in [*all_metrics, *all_holdings, *all_allocations]
    }
    document_versions = {
        item.id: item
        for item in session.scalars(
            select(DocumentVersion).where(DocumentVersion.id.in_(document_ids))
        )
    } if document_ids else {}
    document_types = {
        document_version_id: document_type
        for document_version_id, document_type in session.execute(
            select(DocumentVersion.id, Document.document_type)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentVersion.id.in_(document_ids))
        )
    } if document_ids else {}
    source_labels = {
        version_id: _document_source_label(
            version,
            report_sources,
            document_types.get(version.id),
        )
        for version_id, version in document_versions.items()
    }
    metric_values = {
        id(item): _metric_value(item, source_labels.get(item.document_version_id))
        for item in all_metrics
    }
    holding_values = {
        id(item): HoldingValue(
            security_name=item.security_name,
            ticker=item.ticker,
            holding_quantity=(
                float(item.holding_quantity)
                if item.holding_quantity is not None
                else None
            ),
            weight=float(item.weight) if item.weight is not None else None,
            market_value=float(item.market_value) if item.market_value is not None else None,
            registration_place=item.registration_place,
            holding_type=item.holding_type,
            report_period=item.report_period,
            source_page=item.source_page,
            source_label=source_labels.get(item.document_version_id),
        )
        for item in all_holdings
    }
    allocation_values = {
        id(item): AllocationValue(
            dimension=item.dimension,
            category=item.category,
            weight=float(item.weight) if item.weight is not None else None,
            unit=item.unit,
            report_period=item.report_period,
            source_page=item.source_page,
            source_label=source_labels.get(item.document_version_id),
        )
        for item in all_allocations
    }
    report_periods = sorted(
        {
            item.report_period
            for item in [*all_metrics, *all_holdings, *all_allocations]
        },
        reverse=True,
    )
    # A supplementary interim report can have a separate document version for
    # its portfolio table.  Merge same-period semiannual versions into one UI
    # report while preserving both source records in ``report_sources``.
    report_groups: dict[tuple, dict] = {}
    for item in [*all_metrics, *all_holdings, *all_allocations]:
        source_metadata = _document_source_metadata(item.document_version_id, report_sources)
        report_kind = source_metadata.get("report_kind") or document_types.get(
            document_versions.get(item.document_version_id).id
            if document_versions.get(item.document_version_id)
            else None
        )
        if report_kind == "semiannual":
            group_key = ("semiannual", item.report_period)
        else:
            group_key = ("version", item.document_version_id)
        group = report_groups.setdefault(
            group_key,
            {
                "period": item.report_period,
                "metrics": [],
                "holdings": [],
                "allocations": [],
                "version_ids": [],
            },
        )
        group["period"] = min(group["period"], item.report_period)
        if item.document_version_id not in group["version_ids"]:
            group["version_ids"].append(item.document_version_id)
        if isinstance(item, FundMetric):
            group["metrics"].append(metric_values[id(item)])
        elif isinstance(item, FundHolding):
            group["holdings"].append(holding_values[id(item)])
        else:
            group["allocations"].append(allocation_values[id(item)])
    reports = []
    merged_report_ids: dict[UUID, UUID] = {}
    for group_key, group in sorted(
        report_groups.items(),
        key=lambda item: (item[1]["period"], str(item[0])),
        reverse=True,
    ):
        version_ids = group["version_ids"]
        primary_version_id = version_ids[0]
        if group_key[0] == "semiannual":
            # Prefer the version carrying the portfolio table so the source
            # button opens the richer merged entry; both source cards remain.
            primary_version_id = next(
                (
                    version_id
                    for version_id in version_ids
                    if "投资组合表" in str(
                        _document_source_metadata(version_id, report_sources).get("source_label")
                    )
                ),
                version_ids[0],
            )
            for version_id in version_ids:
                merged_report_ids[version_id] = primary_version_id
            metrics_by_name = {}
            for metric in group["metrics"]:
                existing = metrics_by_name.get(metric.metric_name)
                if existing is None or (
                    existing.numeric_value is None
                    and existing.text_value is None
                    and (metric.numeric_value is not None or metric.text_value is not None)
                ):
                    metrics_by_name[metric.metric_name] = metric
            group["metrics"] = list(metrics_by_name.values())
            # Do not de-duplicate equal-looking lots: a portfolio table can
            # legitimately disclose two lots with identical name, quantity
            # and market value.  The source slice remains the authority for
            # the complete holding count.
            allocations_by_key = {}
            for allocation in group["allocations"]:
                allocations_by_key[(allocation.dimension, allocation.category)] = allocation
            group["allocations"] = list(allocations_by_key.values())
        metadata = _document_source_metadata(primary_version_id, report_sources)
        source_label = source_labels.get(primary_version_id)
        primary_version = document_versions.get(primary_version_id)
        canonical_report_kind = metadata.get("report_kind") or (
            document_types.get(primary_version.id)
            if primary_version is not None
            else None
        )
        if group_key[0] == "semiannual":
            source_label = f"{group['period'].year}年半年报"
        reports.append(
            FundReportData(
                report_period=group["period"],
                document_version_id=primary_version_id,
                source_label=source_label,
                report_kind=canonical_report_kind,
                metrics=group["metrics"],
                holdings=group["holdings"],
                allocations=group["allocations"],
            )
        )
    quality = _quality_from_issues(
        issues,
        has_metrics=bool(metrics),
        has_document=latest_document is not None,
    )
    data = FundDetailData(
        fund_id=fund.id,
        fund_code=fund.fund_code,
        name_cn=fund.name_cn,
        name_en=fund.name_en,
        manager_id=manager.id,
        manager_name=manager.name_cn,
        manager_name_en=manager.name_en,
        manager_short_name=manager.short_name,
        umbrella_name=umbrella.name_cn if umbrella else None,
        umbrella_code=umbrella.umbrella_code if umbrella else None,
        asset_category=classification.level_1 if classification else None,
        asset_subcategory=classification.level_2 if classification else None,
        domicile=fund.domicile,
        base_currency=fund.base_currency,
        management_style=fund.management_style,
        status=fund.status,
        sfc_authorized_at=fund.sfc_authorized_at,
        regulation_category=fund.regulation_category,
        tracking_index=fund.tracking_index,
        quality_status=quality,
        report_period=latest_period,
        metrics=[
            _metric_value(metric, source_labels.get(metric.document_version_id))
            for metric in metrics
        ],
        holdings=[holding_values[id(item)] for item in holdings],
        allocations=[allocation_values[id(item)] for item in allocations],
        reports=reports,
        report_sources=[
            ReportSource(
                source_id=item.source_key,
                source_sheet=item.source_sheet,
                source_row=item.source_row,
                monthly_report_url=item.monthly_report_url,
                fallback_report_url=item.fallback_report_url,
                run_command=item.run_command,
                match_status=item.match_status,
                match_method=item.match_method,
                match_confidence=item.match_confidence,
                is_active=item.is_active,
                is_visible=item.is_visible,
                priority=item.priority,
                semiannual_report_url=item.semiannual_report_url,
                semiannual_run_command=item.semiannual_run_command,
                annual_report_url=item.annual_report_url,
                annual_run_command=item.annual_run_command,
                source_label=(item.metadata_json or {}).get("source_label"),
                report_kind=(item.metadata_json or {}).get("report_kind"),
                report_period=(
                    date.fromisoformat(str((item.metadata_json or {}).get("report_period")))
                    if (item.metadata_json or {}).get("report_period")
                    else None
                ),
                document_version_id=(
                    merged_report_ids.get(
                        UUID(str((item.metadata_json or {}).get("document_version_id"))),
                        UUID(str((item.metadata_json or {}).get("document_version_id"))),
                    )
                    if (item.metadata_json or {}).get("document_version_id")
                    else None
                ),
                language=(item.metadata_json or {}).get("language"),
                available_sections=list((item.metadata_json or {}).get("available_sections") or []),
                download_candidates=[
                    {
                        "rank": candidate.rank,
                        "kind": candidate.kind,
                        "stability": candidate.stability,
                        "source_url": candidate.source_url,
                        "run_command": candidate.run_command,
                        "reason": candidate.reason,
                    }
                    for candidate in sources_for_source(item)
                ],
            )
            for item in report_sources
        ],
        regions=[
            {"level": item.region_level, "name": item.region_name} for item in regions
        ],
        tags=[
            {"type": item.tag_type, "value": item.tag_value} for item in tags
        ],
        stock_codes=stock_codes,
        latest_document=(
            {
                "document_version_id": str(latest_document.DocumentVersion.id),
                "report_date": (
                    latest_document.DocumentVersion.report_date.isoformat()
                    if latest_document.DocumentVersion.report_date
                    else None
                ),
                "source_url": latest_document.DocumentVersion.source_url,
                "file_path": latest_document.DocumentVersion.file_path,
                "sha256": latest_document.DocumentVersion.sha256,
            }
            if latest_document
            else None
        ),
    )
    return data, current.id


def get_fund_metrics(
    session: Session,
    fund_code: str,
) -> tuple[MetricListData, UUID]:
    """读取基金最新指标集；参数 session/fund_code 为查询上下文。"""

    detail, snapshot_id = get_fund_detail(session, fund_code)
    return (
        MetricListData(
            items=detail.metrics,
            report_period=detail.report_period,
            quality_status=detail.quality_status,
        ),
        snapshot_id,
    )


def list_quality_issues(
    session: Session,
    *,
    severity: str | None,
    status: str | None,
    fund_code: str | None,
    page: int,
    page_size: int,
) -> tuple[QualityPageData, UUID]:
    """分页查询当前批次质量问题；参数为过滤和分页条件。"""

    current = get_current_snapshot(session)
    latest_period = (
        select(
            FundMetric.fund_id.label("fund_id"),
            func.max(FundMetric.report_period).label("report_period"),
        )
        .where(FundMetric.batch_id == current.id)
        .group_by(FundMetric.fund_id)
        .subquery()
    )
    base = (
        select(
            QualityIssue,
            Fund.fund_code,
            Fund.name_cn,
            Fund.base_currency,
            Fund.domicile,
            Fund.status.label("fund_status"),
            Manager.name_cn.label("manager_name"),
            FundClassification.level_1.label("asset_category"),
            FundClassification.level_2.label("asset_subcategory"),
            latest_period.c.report_period,
        )
        .outerjoin(Fund, Fund.id == QualityIssue.fund_id)
        .outerjoin(Manager, Manager.id == Fund.manager_id)
        .outerjoin(FundClassification, FundClassification.fund_id == Fund.id)
        .outerjoin(latest_period, latest_period.c.fund_id == Fund.id)
        .where(QualityIssue.batch_id == current.id)
    )
    count_statement = (
        select(func.count())
        .select_from(QualityIssue)
        .outerjoin(Fund, Fund.id == QualityIssue.fund_id)
        .where(QualityIssue.batch_id == current.id)
    )
    conditions = []
    if severity:
        conditions.append(QualityIssue.severity == severity)
    if status:
        conditions.append(QualityIssue.status == status)
    if fund_code:
        conditions.append(Fund.fund_code == fund_code)
    if conditions:
        base = base.where(*conditions)
        count_statement = count_statement.where(*conditions)
    total = int(session.scalar(count_statement) or 0)
    rows = session.execute(
        base.order_by(QualityIssue.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = [
        QualityIssueData(
            issue_id=row.QualityIssue.id,
            fund_id=row.QualityIssue.fund_id,
            fund_code=row.fund_code,
            fund_name=row.name_cn,
            severity=row.QualityIssue.severity,
            issue_type=row.QualityIssue.issue_type,
            details=row.QualityIssue.details,
            status=row.QualityIssue.status,
            created_at=_to_utc(row.QualityIssue.created_at),
            metadata=row.QualityIssue.metadata_json,
            manager_name=row.manager_name,
            asset_category=row.asset_category,
            asset_subcategory=row.asset_subcategory,
            base_currency=row.base_currency,
            domicile=row.domicile,
            fund_status=row.fund_status,
            report_period=row.report_period,
        )
        for row in rows
    ]
    return (
        QualityPageData(
            items=items,
            page=page,
            page_size=page_size,
            total=total,
        ),
        current.id,
    )


def get_perspectives(
    session: Session,
    *,
    fund_codes: list[str] | None,
    manager_id: UUID | None,
    category: str | None,
    period: str | None,
    source_document_version_id: UUID | None,
) -> tuple[PerspectiveData, UUID]:
    """计算确定性 ECharts 视图；可按选中来源版本切换资产结构。"""

    current = get_current_snapshot(session)
    base = select(
        FundScaleHistory.fund_id,
        FundScaleHistory.as_of_date,
        FundScaleHistory.fund_size,
    ).where(
        FundScaleHistory.source_batch_id == current.id,
        # 规模趋势只能展示已发生的报告期；历史旧批次中可能残留
        # 模型把债券到期日误识别成报告期的未来记录。
        FundScaleHistory.as_of_date <= date.today(),
        _month_end_clause(FundScaleHistory.as_of_date),
    )
    if fund_codes:
        base = base.where(
            FundScaleHistory.fund_id.in_(
                select(Fund.id).where(Fund.fund_code.in_(fund_codes))
            )
        )
    if manager_id or category:
        base = base.join(Fund, Fund.id == FundScaleHistory.fund_id)
    if manager_id:
        base = base.where(Fund.manager_id == manager_id)
    if category:
        base = base.join(
            FundClassification,
            FundClassification.fund_id == Fund.id,
        ).where(FundClassification.level_1 == category)
    if period and period != "latest":
        try:
            selected_date = date.fromisoformat(period)
        except ValueError as exc:
            raise ApiError(
                422,
                "E_PERIOD_INVALID",
                "period 必须为 latest 或 ISO 日期",
            ) from exc
        histogram_date = selected_date
    else:
        histogram_date = session.scalar(
            select(func.max(base.subquery().c.as_of_date))
        )
    snapshot_id = str(current.id)
    scope_count = int(
        session.scalar(
            select(func.count(func.distinct(base.subquery().c.fund_id)))
        )
        or 0
    )
    if histogram_date is None:
        return PerspectiveData(views=[], scope_count=0), current.id

    scoped = base.subquery()
    histogram_rows = session.execute(
        select(scoped).where(scoped.c.as_of_date == histogram_date)
    ).all()
    values = [
        float(row.fund_size) for row in histogram_rows if row.fund_size is not None
    ]
    bins = [
        (0, 5, "0–5"),
        (5, 10, "5–10"),
        (10, 25, "10–25"),
        (25, 50, "25–50"),
        (50, 100, "50–100"),
        (100, 250, "100–250"),
        (250, 500, "250–500"),
        (500, None, "500+"),
    ]
    histogram = []
    for start, end, label in bins:
        count = sum(
            1
            for value in values
            if value >= start and (end is None or value < end)
        )
        histogram.append(
            {
                "bin_start": start,
                "bin_end": end,
                "label": label,
                "value": count,
            }
        )
    trend_rows = session.execute(
        select(
            scoped.c.as_of_date,
            func.sum(scoped.c.fund_size).label("value"),
        )
        .group_by(scoped.c.as_of_date)
        .order_by(scoped.c.as_of_date)
    ).all()
    trend = [
        {
            "month_end": row.as_of_date.isoformat(),
            "value": float(row.value) if row.value is not None else None,
        }
        for row in trend_rows
    ]
    allocation_base = select(
        FundAllocation.fund_id,
        FundAllocation.document_version_id,
        FundAllocation.report_period,
        FundAllocation.category,
        FundAllocation.weight,
    ).where(
        FundAllocation.batch_id == current.id,
        FundAllocation.dimension == "asset_type",
        ~exists(
            select(FundReportSource.id).where(
                FundReportSource.fund_id == FundAllocation.fund_id,
                FundReportSource.is_active.is_(True),
                FundReportSource.is_visible.is_(False),
                cast(FundAllocation.document_version_id, String)
                == FundReportSource.metadata_json["document_version_id"].astext,
            )
        ),
    )
    if source_document_version_id:
        allocation_base = allocation_base.where(
            FundAllocation.document_version_id == source_document_version_id
        )
    if fund_codes:
        allocation_base = allocation_base.where(
            FundAllocation.fund_id.in_(
                select(Fund.id).where(Fund.fund_code.in_(fund_codes))
            )
        )
    if manager_id or category:
        allocation_base = allocation_base.join(Fund, Fund.id == FundAllocation.fund_id)
    if manager_id:
        allocation_base = allocation_base.where(Fund.manager_id == manager_id)
    if category:
        allocation_base = allocation_base.join(
            FundClassification,
            FundClassification.fund_id == Fund.id,
        ).where(FundClassification.level_1 == category)
    allocation_scoped = allocation_base.subquery()
    if source_document_version_id:
        allocation_latest = allocation_scoped
    else:
        latest_periods = (
            select(
                allocation_scoped.c.fund_id,
                func.max(allocation_scoped.c.report_period).label("report_period"),
            )
            .group_by(allocation_scoped.c.fund_id)
            .subquery()
        )
        allocation_latest = (
            select(
                allocation_scoped.c.category,
                allocation_scoped.c.weight,
                allocation_scoped.c.report_period,
            )
            .join(
                latest_periods,
                and_(
                    latest_periods.c.fund_id == allocation_scoped.c.fund_id,
                    latest_periods.c.report_period == allocation_scoped.c.report_period,
                ),
            )
            .subquery()
        )
    allocation_rows = session.execute(
        select(
            allocation_latest.c.category,
            func.sum(allocation_latest.c.weight).label("value"),
        )
        .group_by(allocation_latest.c.category)
        .order_by(allocation_latest.c.category)
    ).all()
    allocation_values = merge_allocation_rows(
        [
            {"label": row.category, "value": float(row.value)}
            for row in allocation_rows
            if row.value is not None
        ]
    )
    allocation_period_bounds = session.execute(
        select(
            func.min(allocation_latest.c.report_period),
            func.max(allocation_latest.c.report_period),
            func.count(func.distinct(allocation_latest.c.report_period)),
        )
    ).one()
    allocation_source_period: str | None = None
    if allocation_period_bounds[0] is not None:
        first_period = allocation_period_bounds[0].isoformat()
        last_period = allocation_period_bounds[1].isoformat()
        allocation_source_period = (
            first_period if allocation_period_bounds[2] == 1 else f"{first_period}–{last_period}"
        )
    quality = (
        QualityStatus.COMPLETE if values else QualityStatus.MISSING_METRIC
    )
    views = [
        PerspectiveView(
            view_id="fund_scale_histogram",
            title="当前基金规模直方图",
            chart_type="histogram",
            dimension="fund_size",
            metric="fund_count",
            unit="HKD_100M",
            dataset=histogram,
            snapshot_id=snapshot_id,
            quality=quality,
        ),
        PerspectiveView(
            view_id="fund_scale_trend",
            title="当前基金规模趋势",
            chart_type="line",
            dimension="month_end",
            metric="fund_size",
            unit="HKD_100M",
            dataset=trend,
            snapshot_id=snapshot_id,
            quality=(
                QualityStatus.COMPLETE if trend else QualityStatus.MISSING_METRIC
            ),
        ),
    ]
    if allocation_values:
        views.append(
            PerspectiveView(
                view_id="portfolio_structure",
                title="投资组合结构",
                chart_type="pie",
                dimension="asset_type",
                metric="weight",
                unit="percent",
                dataset=allocation_values,
                source_period=allocation_source_period,
                snapshot_id=snapshot_id,
                quality=QualityStatus.COMPLETE,
            )
        )
    return PerspectiveData(views=views, scope_count=scope_count), current.id

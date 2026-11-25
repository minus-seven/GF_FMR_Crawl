"""Fund browse, detail and metric endpoints."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from pathlib import Path
from uuid import uuid4

import re

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.errors import ApiError
from app.api.responses import envelope
from app.schemas.common import ApiResponse
from app.schemas.funds import FundDetailData, FundPageData, MetricListData
from app.schemas.jobs import (
    FundCollectionData,
    JobCreateRequest,
    JobScopeRequest,
    ScopeMode,
    StageName,
    SupplementarySourceData,
    SourceManagementData,
    SourceManagementItem,
)
from app.models import (
    DocumentSubject,
    Fund,
    FundAllocation,
    FundCollection,
    FundCollectionItem,
    FundHolding,
    FundMetric,
    FundReportSource,
    FundSourceId,
    SourceManagementAudit,
)
from app.services.jobs import create_job, get_job
from app.services.queries import get_current_snapshot
from app.core.config import get_settings
from app.services.queries import get_fund_detail, get_fund_metrics, list_funds


router = APIRouter(prefix="/funds", tags=["funds"])


@router.get("", response_model=ApiResponse[FundPageData])
def fund_list(
    request: Request,
    manager_id: UUID | None = None,
    manager: str | None = None,
    category: str | None = None,
    report_period: date | None = None,
    status: str | None = None,
    q: str | None = None,
    secondary: list[str] = Query(default=[]),
    sort_by: str | None = None,
    sort_direction: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_db),
) -> ApiResponse[FundPageData]:
    """分页查询基金；参数为过滤条件、分页和请求上下文。"""

    data, snapshot_id = list_funds(
        session,
        manager_id=manager_id,
        manager=manager,
        category=category,
        report_period=report_period,
        status=status,
        query=q,
        secondary_filters=secondary,
        sort_by=sort_by,
        sort_direction=sort_direction,
        page=page,
        page_size=page_size,
    )
    return envelope(request, data, snapshot_id)


@router.post("/collections", response_model=ApiResponse[FundCollectionData], status_code=201)
async def create_fund_collection(request: Request, session: Session = Depends(get_db)) -> ApiResponse[FundCollectionData]:
    """创建绑定会话的临时基金集合；参数 request/session 为请求上下文。"""

    payload = await request.json()
    session_key = str(payload.get("session_key") or "").strip()
    fund_codes = list(dict.fromkeys(str(item).strip() for item in payload.get("fund_codes") or [] if str(item).strip()))
    if not session_key or not fund_codes:
        raise ApiError(422, "E_COLLECTION_INVALID", "session_key 与 fund_codes 不能为空")
    session.execute(delete(FundCollection).where(FundCollection.expires_at <= datetime.now(timezone.utc)))
    funds = list(session.scalars(select(Fund).where(Fund.fund_code.in_(fund_codes))))
    by_code = {fund.fund_code: fund for fund in funds}
    if len(by_code) != len(fund_codes):
        raise ApiError(422, "E_COLLECTION_FUND_NOT_FOUND", "集合中存在不存在的基金代码")
    now = datetime.now(timezone.utc)
    collection = FundCollection(
        id=uuid4(),
        created_at=now,
        updated_at=now,
        session_key=session_key,
        label=str(payload.get("label") or "已选基金集合")[:300],
        expires_at=now + timedelta(hours=24),
        status="active",
    )
    session.add(collection)
    session.flush()
    for code in fund_codes:
        fund = by_code[code]
        session.add(FundCollectionItem(id=uuid4(), created_at=now, updated_at=now, collection_id=collection.id, fund_id=fund.id, fund_code_snapshot=code, fund_name_snapshot=fund.name_cn))
    session.commit()
    return envelope(request, FundCollectionData(collection_id=collection.id, label=collection.label, fund_codes=fund_codes, count=len(fund_codes), expires_at=collection.expires_at), None)


@router.get("/collections", response_model=ApiResponse[list[FundCollectionData]])
def list_fund_collections(
    request: Request,
    session_key: str = Query(..., min_length=1, max_length=200),
    session: Session = Depends(get_db),
) -> ApiResponse[list[FundCollectionData]]:
    """列出当前会话仍有效的临时基金集合；参数为会话键和数据库上下文。"""

    now = datetime.now(timezone.utc)
    session.execute(delete(FundCollection).where(FundCollection.expires_at <= now))
    collections = list(
        session.scalars(
            select(FundCollection)
            .where(
                FundCollection.session_key == session_key.strip(),
                FundCollection.status == "active",
                FundCollection.expires_at > now,
            )
            .order_by(FundCollection.created_at.desc())
        )
    )
    result: list[FundCollectionData] = []
    for collection in collections:
        items = list(
            session.scalars(
                select(FundCollectionItem)
                .where(FundCollectionItem.collection_id == collection.id)
                .order_by(FundCollectionItem.fund_code_snapshot)
            )
        )
        result.append(
            FundCollectionData(
                collection_id=collection.id,
                label=collection.label,
                fund_codes=[item.fund_code_snapshot for item in items],
                count=len(items),
                expires_at=collection.expires_at,
            )
        )
    session.commit()
    return envelope(request, result, None)


@router.get("/{fund_code}/sources/manage", response_model=ApiResponse[SourceManagementData])
def manage_sources_list(fund_code: str, request: Request, session: Session = Depends(get_db)) -> ApiResponse[SourceManagementData]:
    """返回基金全部来源（包含隐藏）；参数 fund_code/request/session 为上下文。"""

    fund = session.scalar(select(Fund).where(Fund.fund_code == fund_code))
    if fund is None:
        raise ApiError(404, "E_FUND_NOT_FOUND", "基金不存在")
    sources = list(session.scalars(select(FundReportSource).where(FundReportSource.fund_id == fund.id).order_by(FundReportSource.priority, FundReportSource.created_at.desc())))
    items = []
    for source in sources:
        version_id = (source.metadata_json or {}).get("document_version_id")
        metric_count = holding_count = 0
        if version_id:
            try:
                from uuid import UUID as _UUID
                version_uuid = _UUID(str(version_id))
                metric_count = int(session.scalar(select(func.count()).select_from(FundMetric).where(FundMetric.fund_id == fund.id, FundMetric.document_version_id == version_uuid)) or 0)
                holding_count = int(session.scalar(select(func.count()).select_from(FundHolding).where(FundHolding.fund_id == fund.id, FundHolding.document_version_id == version_uuid)) or 0)
            except ValueError:
                pass
        items.append(SourceManagementItem(source_id=source.source_key, label=(source.metadata_json or {}).get("source_label") or source.source_key, report_kind=(source.metadata_json or {}).get("report_kind") or source.report_kind, report_period=str((source.metadata_json or {}).get("report_period") or "") or None, is_visible=source.is_visible, is_active=source.is_active, document_version_id=version_id, metric_count=metric_count, holding_count=holding_count))
    return envelope(request, SourceManagementData(fund_code=fund_code, items=items), None)


@router.post("/{fund_code}/sources/manage", response_model=ApiResponse[SourceManagementData])
async def manage_sources_action(fund_code: str, request: Request, session: Session = Depends(get_db)) -> ApiResponse[SourceManagementData]:
    """执行来源显示、隐藏或删除；删除仅移除基金关联及派生数据。"""

    payload = await request.json()
    action = str(payload.get("action") or "").strip().lower()
    source_ids = list(dict.fromkeys(str(item).strip() for item in payload.get("source_ids") or [] if str(item).strip()))
    if action not in {"hide", "show", "delete"} or not source_ids:
        raise ApiError(422, "E_SOURCE_ACTION", "一次只能选择 hide、show、delete 且至少选择一个来源")
    fund = session.scalar(select(Fund).where(Fund.fund_code == fund_code))
    if fund is None:
        raise ApiError(404, "E_FUND_NOT_FOUND", "基金不存在")
    sources = list(session.scalars(select(FundReportSource).where(FundReportSource.fund_id == fund.id, FundReportSource.source_key.in_(source_ids))))
    if len(sources) != len(source_ids):
        raise ApiError(404, "E_SOURCE_NOT_FOUND", "部分来源不存在")
    actor = request.headers.get("x-user-id") or request.client.host if request.client else "api"
    for source in sources:
        version_id = (source.metadata_json or {}).get("document_version_id")
        details = {"fund_code": fund_code, "document_version_id": version_id, "source_label": (source.metadata_json or {}).get("source_label")}
        now = datetime.now(timezone.utc)
        session.add(SourceManagementAudit(id=uuid4(), created_at=now, updated_at=now, fund_id=fund.id, source_key=source.source_key, action=action, actor=actor, details=details))
        if action == "hide":
            source.is_visible = False
        elif action == "show":
            source.is_visible = True
        else:
            if version_id:
                from uuid import UUID as _UUID
                try:
                    version_uuid = _UUID(str(version_id))
                    for model in (FundMetric, FundHolding, FundAllocation):
                        session.execute(delete(model).where(model.fund_id == fund.id, model.document_version_id == version_uuid))
                    session.execute(delete(DocumentSubject).where(DocumentSubject.fund_id == fund.id, DocumentSubject.document_version_id == version_uuid))
                except ValueError:
                    pass
            session.delete(source)
    session.commit()
    return manage_sources_list(fund_code, request, session)


@router.get(
    "/{fund_code}/metrics",
    response_model=ApiResponse[MetricListData],
)
def fund_metrics(
    fund_code: str,
    request: Request,
    session: Session = Depends(get_db),
) -> ApiResponse[MetricListData]:
    """返回基金最新结构化指标；参数 fund_code/request/session 为请求上下文。"""

    data, snapshot_id = get_fund_metrics(session, fund_code)
    return envelope(request, data, snapshot_id)


@router.post(
    "/{fund_code}/supplementary-source",
    response_model=ApiResponse[SupplementarySourceData],
    status_code=202,
)
async def create_supplementary_source(
    fund_code: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_db),
) -> ApiResponse[SupplementarySourceData]:
    """创建单基金补充来源任务；支持 URL 或上传 PDF，并复用三段流水线。"""

    content_type = request.headers.get("content-type", "")
    fields: dict[str, str] = {}
    file_name: str | None = None
    file_bytes: bytes | None = None
    if content_type.startswith("multipart/form-data"):
        match = re.search(r"boundary=([^;]+)", content_type)
        if not match:
            raise ApiError(422, "E_SUPPLEMENTARY_SOURCE", "multipart 缺少 boundary")
        boundary = match.group(1).strip('"').encode()
        body = await request.body()
        for part in body.split(b"--" + boundary):
            if b"\r\n\r\n" not in part:
                continue
            header_blob, value = part.split(b"\r\n\r\n", 1)
            header_text = header_blob.decode("utf-8", errors="ignore")
            disposition = re.search(r'name="([^"]+)"(?:; filename="([^"]*)")?', header_text)
            if not disposition:
                continue
            name, filename = disposition.group(1), disposition.group(2)
            if value.endswith(b"\r\n"):
                value = value[:-2]
            if filename is not None:
                file_name = filename
                file_bytes = value
            else:
                fields[name] = value.decode("utf-8", errors="replace")
    else:
        try:
            payload = await request.json()
        except Exception as exc:  # noqa: BLE001
            raise ApiError(422, "E_SUPPLEMENTARY_SOURCE", "请求必须是 JSON 或 multipart/form-data") from exc
        fields = {str(key): str(value) for key, value in payload.items() if value is not None}
    source_url = fields.get("source_url", "").strip()
    structured_provider = fields.get("structured_provider", "local")
    fixed_candidate = fields.get("fixed_candidate", "true").lower() in {"1", "true", "yes", "on"}

    url = (source_url or "").strip()
    if file_bytes is None and not url:
        raise ApiError(422, "E_SUPPLEMENTARY_SOURCE", "source_url 或 source_file 至少提供一项")
    if file_bytes is not None and not (file_name or "").lower().endswith(".pdf"):
        raise ApiError(422, "E_SUPPLEMENTARY_SOURCE", "补充来源文件必须是 PDF")
    if url and not url.startswith(("http://", "https://")):
        raise ApiError(422, "E_SUPPLEMENTARY_SOURCE", "补充来源 URL 必须为 http/https")
    if file_bytes is not None and not file_bytes.startswith(b"%PDF"):
        raise ApiError(422, "E_SUPPLEMENTARY_SOURCE", "上传文件不是有效 PDF")
    if file_bytes is not None and len(file_bytes) > 100 * 1024 * 1024:
        raise ApiError(413, "E_SUPPLEMENTARY_SOURCE", "补充来源 PDF 不能超过 100 MB")
    provider = structured_provider.strip().lower()
    if provider not in {"local", "deepseek", "qwen"}:
        raise ApiError(422, "E_SUPPLEMENTARY_SOURCE", "structured_provider 不受支持")
    source_kind = "upload" if file_bytes is not None else "url"
    policy = {
        "supplementary_source": {
            "source_kind": source_kind,
            "source_url": url or None,
            "fixed_candidate": bool(fixed_candidate),
            "original_filename": file_name,
            "input_path": None,
        }
    }
    body = JobCreateRequest(
        scope=JobScopeRequest(mode=ScopeMode.FUNDS, fund_codes=[fund_code]),
        stages=[StageName.DOWNLOAD, StageName.PARSE, StageName.STRUCTURE],
        document_policy=policy,
        model_policy={"structured_provider": provider},
    )
    key = (idempotency_key or f"supplementary-{fund_code}-{uuid4().hex}").strip()
    current = get_current_snapshot(session)
    job, created = create_job(session, body, idempotency_key=key)
    if created and file_bytes is not None:
        contents = file_bytes
        target = get_settings().data_root / "jobs" / str(job.id) / "supplementary_input" / "input.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents)
        scope_definition = dict(job.scope_definition or {})
        document_policy = dict(scope_definition.get("document_policy") or {})
        supplementary = dict(document_policy.get("supplementary_source") or {})
        supplementary["input_path"] = (Path("data") / target.relative_to(get_settings().data_root)).as_posix()
        document_policy["supplementary_source"] = supplementary
        scope_definition["document_policy"] = document_policy
        job.scope_definition = scope_definition
        session.commit()
    data = SupplementarySourceData(
        job=get_job(session, job.id),
        estimated_minutes=5 if provider == "local" else 12,
        source_kind=source_kind,
    )
    return envelope(request, data, current.id)


@router.get("/{fund_code}", response_model=ApiResponse[FundDetailData])
def fund_detail(
    fund_code: str,
    request: Request,
    session: Session = Depends(get_db),
) -> ApiResponse[FundDetailData]:
    """返回基金确定性和补充来源详情；参数 fund_code/request/session。"""

    data, snapshot_id = get_fund_detail(session, fund_code)
    return envelope(request, data, snapshot_id)

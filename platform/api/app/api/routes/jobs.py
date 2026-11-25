"""Task creation, query, cancellation and SSE endpoints."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_session_factory
from app.api.errors import ApiError
from app.api.responses import envelope
from app.core.config import get_settings
from app.importers.service import D1ImportService
from app.models import Job, JobEvent
from app.schemas.common import ApiResponse
from app.schemas.jobs import (
    JobCreateRequest,
    JobData,
    JobFundIssuePageData,
    JobFundScopePageData,
    JobEventPageData,
    JobPageData,
    MasterDatabaseImportData,
    MasterDatabaseOptionsData,
    PublishRequest,
    PublishResultData,
    ScopeOptionsData,
)
from app.services.jobs import (
    TERMINAL_JOB_STATUSES,
    cancel_job,
    create_job,
    get_job,
    get_scope_options,
    list_job_fund_issues,
    list_job_funds,
    list_job_events,
    list_jobs,
    retry_job,
)
from app.services.queries import get_current_snapshot
from app.services.publication import publish_job_manifest


router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get(
    "/master-database/options",
    response_model=ApiResponse[MasterDatabaseOptionsData],
)
def master_database_options(
    request: Request,
    session: Session = Depends(get_db),
) -> ApiResponse[MasterDatabaseOptionsData]:
    """返回综合资料库参考文件候选；参数 request/session 为 API 上下文。"""

    settings = get_settings()
    candidates: list[Path] = []
    for root in (
        settings.project_root,
        settings.state_root / "inputs",
        settings.hkex_crawler_root,
        settings.hkex_crawler_root / "doc" if settings.hkex_crawler_root else None,
    ):
        if root and root.is_dir():
            candidates.extend(sorted(root.glob("*.xlsx")))
    unique: list[str] = []
    seen: set[str] = set()
    for path in candidates:
        value = str(path.resolve())
        if value not in seen:
            seen.add(value)
            unique.append(value)
    default = settings.hkex_workbook or settings.project_root / "HKEX基金综合资料库_完整_20261008.xlsx"
    if not default.is_file() and unique:
        default = Path(unique[0])
    data = MasterDatabaseOptionsData(
        default_reference_file=str(default),
        reference_files=unique,
        crawler_root=str(settings.hkex_crawler_root) if settings.hkex_crawler_root else None,
        crawler_available=bool(settings.hkex_crawler_root and (settings.hkex_crawler_root / "crawler.py").is_file()),
    )
    current = get_current_snapshot(session)
    return envelope(request, data, current.id)


def _master_artifact_path(job_id: UUID, batch_id: UUID | None) -> Path | None:
    """定位已发布或 staging 中的综合资料库 xlsx。"""

    root = get_settings().data_root
    roots = []
    if batch_id:
        roots.append(root / "batches" / str(batch_id) / "staging" / str(job_id) / "master_database")
    roots.append(root / "jobs" / str(job_id) / "staging" / str(job_id) / "master_database")
    for directory in roots:
        if not directory.is_dir():
            continue
        # 下载名使用参考文件模板和更新日期；latest 仅作为内部兼容回退。
        dated = [
            path for path in directory.glob("*.xlsx")
            if path.name != "HKEX基金综合资料库_latest.xlsx"
        ]
        if dated:
            return max(dated, key=lambda path: path.stat().st_mtime)
        latest = directory / "HKEX基金综合资料库_latest.xlsx"
        if latest.is_file():
            return latest
    return None


@router.get("/{job_id}/master-artifact")
def master_database_artifact(
    job_id: UUID,
    session: Session = Depends(get_db),
) -> FileResponse:
    """下载综合资料库任务缓存的最新 xlsx。"""

    job = session.get(Job, job_id)
    if job is None or job.task_target != "master_database":
        raise ApiError(404, "E_MASTER_JOB_NOT_FOUND", "综合资料库任务不存在")
    path = _master_artifact_path(job_id, job.batch_id)
    if path is None:
        raise ApiError(404, "E_MASTER_ARTIFACT_NOT_FOUND", "任务尚未生成综合资料库缓存文件")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=path.name,
    )


@router.post(
    "/{job_id}/master-import",
    response_model=ApiResponse[MasterDatabaseImportData],
)
def import_master_database(
    job_id: UUID,
    request: Request,
    session: Session = Depends(get_db),
) -> ApiResponse[MasterDatabaseImportData]:
    """把已完成任务的缓存 xlsx 导入为新的发布批次。"""

    job = session.get(Job, job_id)
    if job is None or job.task_target != "master_database":
        raise ApiError(404, "E_MASTER_JOB_NOT_FOUND", "综合资料库任务不存在")
    if job.status != "completed":
        raise ApiError(409, "E_MASTER_JOB_NOT_COMPLETED", "任务完成后才能落库")
    active_job = session.scalar(
        select(Job.id)
        .where(Job.status.in_({"waiting_lock", "queued", "running", "validating", "publishing"}))
        .limit(1)
    )
    if active_job is not None:
        raise ApiError(409, "E_MASTER_IMPORT_BUSY", "当前仍有任务运行，完成后再执行综合资料库落库")
    path = _master_artifact_path(job_id, job.batch_id)
    if path is None:
        raise ApiError(404, "E_MASTER_ARTIFACT_NOT_FOUND", "任务尚未生成综合资料库缓存文件")
    service = D1ImportService(session, project_root=get_settings().project_root)
    summary = service.import_hkex_master_workbook(path, activate=True)
    session.commit()
    current = get_current_snapshot(session)
    data = MasterDatabaseImportData(
        job_id=job.id,
        workbook_path=str(path.relative_to(get_settings().project_root)),
        summary=summary.to_dict(),
    )
    return envelope(request, data, current.id)


@router.post(
    "/{job_id}/publish",
    response_model=ApiResponse[PublishResultData],
)
def internal_publish(
    job_id: UUID,
    body: PublishRequest,
    request: Request,
    session: Session = Depends(get_db),
) -> ApiResponse[PublishResultData]:
    """供本机 Worker 提交已验证 manifest；参数 job_id/body/request/session。"""

    data = publish_job_manifest(
        session,
        job_id=job_id,
        manifest_path=body.manifest_path,
        manifest_sha256=body.manifest_sha256,
        lease_token=body.lease_token,
    )
    return envelope(request, data, data.current_snapshot_id)


@router.post("", response_model=ApiResponse[JobData])
def submit_job(
    body: JobCreateRequest,
    request: Request,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    session: Session = Depends(get_db),
) -> ApiResponse[JobData]:
    """创建任务并冻结范围；参数 body/request/response/key/session。"""

    current = get_current_snapshot(session)
    job, created = create_job(
        session,
        body,
        idempotency_key=idempotency_key,
    )
    response.status_code = 202 if created else 200
    data = get_job(session, job.id)
    return envelope(request, data, current.id)


@router.get("", response_model=ApiResponse[JobPageData])
def job_list(
    request: Request,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_db),
) -> ApiResponse[JobPageData]:
    """分页查询任务；参数 request/status/page/page_size/session。"""

    current = get_current_snapshot(session)
    return envelope(
        request,
        list_jobs(
            session,
            status=status,
            page=page,
            page_size=page_size,
        ),
        current.id,
    )


@router.get("/scope-options", response_model=ApiResponse[ScopeOptionsData])
def scope_options(
    request: Request,
    session: Session = Depends(get_db),
) -> ApiResponse[ScopeOptionsData]:
    """返回全量、管理人和资产类别范围选项；参数 request/session。"""

    current = get_current_snapshot(session)
    return envelope(request, get_scope_options(session), current.id)


@router.get("/{job_id}", response_model=ApiResponse[JobData])
def job_detail(
    job_id: UUID,
    request: Request,
    session: Session = Depends(get_db),
) -> ApiResponse[JobData]:
    """查询任务详情；参数 job_id/request/session。"""

    current = get_current_snapshot(session)
    return envelope(request, get_job(session, job_id), current.id)


@router.post("/{job_id}/cancel", response_model=ApiResponse[JobData])
def cancel(
    job_id: UUID,
    request: Request,
    session: Session = Depends(get_db),
) -> ApiResponse[JobData]:
    """请求取消任务；参数 job_id/request/session。"""

    current = get_current_snapshot(session)
    return envelope(request, cancel_job(session, job_id), current.id)


@router.post("/{job_id}/retry", response_model=ApiResponse[JobData])
def retry(
    job_id: UUID,
    request: Request,
    session: Session = Depends(get_db),
) -> ApiResponse[JobData]:
    """从失败阶段继续任务；参数 job_id/request/session。"""

    current = get_current_snapshot(session)
    return envelope(request, retry_job(session, job_id), current.id)


@router.get(
    "/{job_id}/funds",
    response_model=ApiResponse[JobFundScopePageData],
)
def job_funds(
    job_id: UUID,
    request: Request,
    result_status: list[str] | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=1000),
    session: Session = Depends(get_db),
) -> ApiResponse[JobFundScopePageData]:
    """查询任务内基金结果和跳过/失败原因；参数 job/filter/page/session。"""

    current = get_current_snapshot(session)
    return envelope(
        request,
        list_job_funds(
            session,
            job_id,
            result_statuses=result_status,
            page=page,
            page_size=page_size,
        ),
        current.id,
    )


@router.get(
    "/{job_id}/issues",
    response_model=ApiResponse[JobFundIssuePageData],
)
def job_issues(
    job_id: UUID,
    request: Request,
    session: Session = Depends(get_db),
) -> ApiResponse[JobFundIssuePageData]:
    """查询任务各阶段跳过与失败基金；参数 job_id/request/session。"""

    current = get_current_snapshot(session)
    return envelope(
        request,
        list_job_fund_issues(session, job_id),
        current.id,
    )


@router.get(
    "/{job_id}/events/history",
    response_model=ApiResponse[JobEventPageData],
)
def job_event_history(
    job_id: UUID,
    request: Request,
    limit: int = Query(default=200, ge=1, le=500),
    session: Session = Depends(get_db),
) -> ApiResponse[JobEventPageData]:
    """返回任务最近事件并提供 SSE 起始序号；参数 job_id/request/limit/session 为查询上下文。"""

    current = get_current_snapshot(session)
    return envelope(
        request,
        list_job_events(session, job_id, limit=limit, latest=True),
        current.id,
    )


@router.get("/{job_id}/events")
async def job_events(
    job_id: UUID,
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    """以 SSE 推送任务事件；参数 job_id/request/last_event_id。"""

    factory = get_session_factory()
    with factory() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise ApiError(404, "E_JOB_NOT_FOUND", "任务不存在")
        cursor = after_sequence
        if last_event_id:
            if last_event_id.isdigit():
                cursor = int(last_event_id)
            else:
                try:
                    event_uuid = UUID(last_event_id)
                except ValueError:
                    raise ApiError(
                        422,
                        "E_LAST_EVENT_INVALID",
                        "Last-Event-ID 格式无效",
                    )
                cursor = int(
                    session.scalar(
                        select(JobEvent.sequence_no).where(
                            JobEvent.id == event_uuid,
                            JobEvent.job_id == job_id,
                        )
                    )
                    or 0
                )

    async def stream():
        sequence = cursor
        idle_ticks = 0
        while True:
            with factory() as session:
                page = list_job_events(
                    session,
                    job_id,
                    after_sequence=sequence,
                    limit=200,
                )
                current = session.get(Job, job_id)
            for event in page.items:
                sequence = event.sequence_no
                payload = event.model_dump(mode="json")
                yield (
                    f"id: {event.event_id}\n"
                    f"event: {event.type}\n"
                    f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                )
            if current is not None and current.status in TERMINAL_JOB_STATUSES:
                if not page.items:
                    break
            idle_ticks += 1
            if idle_ticks % 15 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

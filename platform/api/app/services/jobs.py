"""Task creation, scope freezing, locks and event queries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import delete, desc, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.importers.common import sha256_json
from app.models import (
    DatasetBatch,
    Fund,
    FundCollection,
    FundCollectionItem,
    FundClassification,
    FundTag,
    Job,
    JobEvent,
    JobFundScope,
    JobLock,
    JobStep,
    Manager,
)
from app.schemas.jobs import (
    CategoryOptionData,
    JobCreateRequest,
    JobData,
    JobEventData,
    JobEventPageData,
    JobFundIssueData,
    JobFundIssuePageData,
    JobFundScopeItemData,
    JobFundScopePageData,
    JobLockData,
    JobPageData,
    JobProgress,
    JobScopeDisplayData,
    JobStepData,
    ManagerOptionData,
    ScopeOptionsData,
    ScopeMode,
    TaskTarget,
)
from app.services.model_settings import read_model_settings
from app.services.queries import get_current_snapshot


TERMINAL_JOB_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "publish_failed",
}
LOCK_MINUTES = 10


def _utc_now() -> datetime:
    """返回 UTC 当前时间；参数为空。"""

    return datetime.now(timezone.utc)


def _select_scope_funds(
    session: Session,
    request: JobCreateRequest,
) -> list[Fund]:
    """按冻结条件选择基金；参数 session/request 为查询上下文。"""

    statement = select(Fund)
    scope = request.scope
    if scope.mode == ScopeMode.MANAGER:
        statement = statement.where(Fund.manager_id.in_(scope.manager_ids))
    elif scope.mode == ScopeMode.CATEGORY:
        statement = statement.join(
            FundClassification,
            FundClassification.fund_id == Fund.id,
        ).where(FundClassification.level_1.in_(scope.categories))
    elif scope.mode == ScopeMode.FUNDS:
        statement = statement.where(Fund.fund_code.in_(scope.fund_codes))
    elif scope.mode == ScopeMode.TAG:
        tagged_funds = select(FundTag.fund_id).where(
            FundTag.tag_value.in_(scope.tag_filters)
        )
        statement = statement.where(Fund.id.in_(tagged_funds))
    elif scope.mode == ScopeMode.COLLECTION:
        collection = session.get(FundCollection, scope.collection_id)
        if collection is None or collection.status != "active" or collection.expires_at <= _utc_now():
            raise ApiError(422, "E_COLLECTION_EXPIRED", "基金集合不存在、已过期或已清理")
        if scope.collection_session_key and collection.session_key != scope.collection_session_key:
            raise ApiError(403, "E_COLLECTION_SESSION", "基金集合不属于当前会话")
        members = select(FundCollectionItem.fund_id).where(
            FundCollectionItem.collection_id == collection.id
        )
        statement = statement.where(Fund.id.in_(members))
    funds = list(
        session.scalars(statement.distinct().order_by(Fund.fund_code)).unique()
    )
    if not funds:
        raise ApiError(422, "E_SCOPE_EMPTY", "筛选后没有基金")
    return funds


def get_scope_options(session: Session) -> ScopeOptionsData:
    """返回任务中心管理人和资产类别选项；参数 session 为查询上下文。"""

    total_funds = int(session.scalar(select(func.count()).select_from(Fund)) or 0)
    manager_rows = session.execute(
        select(
            Manager.id,
            Manager.name_cn,
            func.count(Fund.id).label("fund_count"),
        )
        .join(Fund, Fund.manager_id == Manager.id)
        .group_by(Manager.id, Manager.name_cn)
        .order_by(Manager.name_cn)
    ).all()
    category_rows = session.execute(
        select(
            FundClassification.level_1.label("category"),
            func.count(func.distinct(FundClassification.fund_id)).label(
                "fund_count"
            ),
        )
        .where(FundClassification.level_1.is_not(None))
        .group_by(FundClassification.level_1)
        .order_by(FundClassification.level_1)
    ).all()
    return ScopeOptionsData(
        total_funds=total_funds,
        managers=[
            ManagerOptionData(
                manager_id=row.id,
                manager_name=row.name_cn,
                fund_count=int(row.fund_count),
            )
            for row in manager_rows
        ],
        categories=[
            CategoryOptionData(
                category=row.category,
                fund_count=int(row.fund_count),
            )
            for row in category_rows
        ],
    )


def _next_sequence(session: Session, job_id: uuid.UUID) -> int:
    """返回下一事件序号；参数 session/job_id 为任务上下文。"""

    current = session.scalar(
        select(func.max(JobEvent.sequence_no)).where(JobEvent.job_id == job_id)
    )
    return int(current or 0) + 1


def append_event(
    session: Session,
    job: Job,
    event_type: str,
    payload: dict,
) -> JobEvent:
    """追加任务事件；参数 session/job/event_type/payload 为事件内容。"""

    event = JobEvent(
        id=uuid.uuid4(),
        job_id=job.id,
        sequence_no=_next_sequence(session, job.id),
        event_type=event_type,
        payload=payload,
        created_at=_utc_now(),
    )
    session.add(event)
    session.flush()
    return event


def _acquire_lock(session: Session, job: Job, owner: str) -> bool:
    """尝试取得全局写锁；参数 session/job/owner 为任务与实例。"""

    now = _utc_now()
    active_cutoff = now - timedelta(minutes=LOCK_MINUTES)
    active_compute = session.scalar(
        select(Job.id)
        .where(
            Job.status.in_({"running", "validating", "publishing"}),
            Job.id != job.id,
            Job.last_heartbeat_at.is_not(None),
            Job.last_heartbeat_at > active_cutoff,
        )
        .limit(1)
    )
    # 计算阶段会暂时释放 global_write；此时不能让 API 把锁交给
    # waiting/queued 任务，否则单 Worker 会出现“当前任务等待发布锁、
    # 下一任务持有锁但无人可执行”的死锁。
    if active_compute is not None:
        return False
    session.execute(
        delete(JobLock).where(JobLock.expires_at <= now)
    )
    result = session.execute(
        pg_insert(JobLock)
        .values(
            id=uuid.uuid4(),
            job_id=job.id,
            lock_name="global_write",
            owner=owner,
            heartbeat_at=now,
            expires_at=now + timedelta(minutes=LOCK_MINUTES),
        )
        .on_conflict_do_nothing(index_elements=[JobLock.lock_name])
        .returning(JobLock.id)
    ).scalar_one_or_none()
    return result is not None


def create_job(
    session: Session,
    request: JobCreateRequest,
    *,
    idempotency_key: str,
    owner: str = "api",
) -> tuple[Job, bool]:
    """创建任务并冻结基金范围；参数 session/request/key/owner 为请求。"""

    key = idempotency_key.strip()
    if not key or len(key) > 128:
        raise ApiError(422, "E_IDEMPOTENCY_KEY", "Idempotency-Key 长度必须为 1–128")
    payload = request.model_dump(mode="json")
    stage_names = {stage.value for stage in request.stages}
    if request.task_target == TaskTarget.MASTER_DATABASE:
        if stage_names != {"download"}:
            raise ApiError(
                422,
                "E_MASTER_STAGE_INVALID",
                "综合资料库任务只支持下载并缓存阶段",
            )
    model_policy = dict(payload.get("model_policy") or {})
    if "structure" in stage_names:
        if not model_policy:
            settings = read_model_settings()
            model_policy = {
                "structured_provider": settings.structured_provider,
            }
            if settings.structured_provider == "local":
                model_policy.update(
                    {
                        "local_model": settings.local.primary_model,
                        "local_workers": settings.local.workers,
                    }
                )
        elif model_policy.get("structured_provider") == "local":
            model_policy["local_model"] = str(
                model_policy.get("local_model") or "qwen3:4b"
            ).strip()
            model_policy["local_workers"] = max(
                1,
                min(2, int(model_policy.get("local_workers", 1))),
            )
    else:
        model_policy = {}
    payload["model_policy"] = model_policy
    request_hash = sha256_json(payload)
    existing = session.scalar(select(Job).where(Job.idempotency_key == key))
    if existing:
        if existing.request_hash != request_hash:
            raise ApiError(
                409,
                "E_IDEMPOTENCY_CONFLICT",
                "同一 Idempotency-Key 被用于不同请求",
            )
        return existing, False

    funds = _select_scope_funds(session, request)
    if "download" in stage_names and request.input_job_id is not None:
        raise ApiError(
            422,
            "E_INPUT_JOB_INVALID",
            "包含 download 阶段的完整任务不能引用前序任务",
        )
    source_job = None
    if request.input_job_id is not None:
        source_job = session.get(Job, request.input_job_id)
        if source_job is None or source_job.status not in TERMINAL_JOB_STATUSES:
            raise ApiError(
                422,
                "E_INPUT_JOB_INVALID",
                "前序任务不存在或尚未结束",
            )
        source_fund_ids = set(
            session.scalars(
                select(JobFundScope.fund_id).where(
                    JobFundScope.job_id == source_job.id
                )
            )
        )
        requested_fund_ids = {fund.id for fund in funds}
        if not requested_fund_ids.issubset(source_fund_ids):
            raise ApiError(
                422,
                "E_INPUT_SCOPE_NOT_SUBSET",
                "续跑任务的基金范围必须是前序任务范围的子集",
            )
        source_stages = set(source_job.requested_stages or [])
        if "parse" in stage_names and "download" not in source_stages:
            raise ApiError(
                422,
                "E_INPUT_STAGE_MISSING",
                "前序任务没有下载阶段产物",
            )
        completed_source_steps = set(
            session.scalars(
                select(JobStep.step_type).where(
                    JobStep.job_id == source_job.id,
                    JobStep.status == "completed",
                )
            )
        )
        if "parse" in stage_names and "download" not in completed_source_steps:
            raise ApiError(
                422,
                "E_INPUT_STAGE_MISSING",
                "前序任务没有成功完成的下载阶段",
            )
        if (
            "structure" in stage_names
            and "parse" not in stage_names
            and not ({"parse", "structure"} & completed_source_steps)
        ):
            raise ApiError(
                422,
                "E_INPUT_STAGE_MISSING",
                "前序任务没有成功完成的解析或结构化阶段",
            )
    elif "download" not in stage_names:
        raise ApiError(
            422,
            "E_INPUT_JOB_REQUIRED",
            "仅解析或结构化任务必须引用已完成的前序任务",
        )

    current = get_current_snapshot(session)
    now = _utc_now()
    job = Job(
        id=uuid.uuid4(),
        requested_by=None,
        status="waiting_lock",
        base_batch_id=current.id,
        requested_stages=[stage.value for stage in request.stages],
        task_target=request.task_target.value,
        scope_definition={
            **payload["scope"],
            "task_target": request.task_target.value,
            "input_job_id": str(source_job.id) if source_job else None,
            "model_policy": model_policy,
            "document_policy": dict(payload.get("document_policy") or {}),
        },
        scope_hash=sha256_json({"scope": payload["scope"], "task_target": request.task_target.value}),
        idempotency_key=key,
        request_hash=request_hash,
        cancellation_requested=False,
        created_at=now,
    )
    session.add(job)
    session.flush()

    batch = DatasetBatch(
        id=uuid.uuid4(),
        job_id=job.id,
        status="staging",
        scope_hash=job.scope_hash,
        source_kind="task",
        source_sha256=current.batch.source_sha256,
        coverage=None,
    )
    session.add(batch)
    session.flush()
    job.batch_id = batch.id

    for fund in funds:
        session.add(
            JobFundScope(
                id=uuid.uuid4(),
                job_id=job.id,
                fund_id=fund.id,
                fund_code_snapshot=fund.fund_code,
                fund_name_snapshot=fund.name_cn,
                result_status="pending",
            )
        )
    for stage in request.stages:
        session.add(
            JobStep(
                id=uuid.uuid4(),
                job_id=job.id,
                step_type=stage.value,
                status="pending",
                total_count=len(funds),
                success_count=0,
                skip_count=0,
                failure_count=0,
            )
        )
    append_event(
        session,
        job,
        "job.snapshot",
        {
            "status": "waiting_lock",
            "scope_count": len(funds),
            "stages": job.requested_stages,
            "batch_id": str(batch.id),
        },
    )

    if _acquire_lock(session, job, owner):
        job.status = "queued"
        append_event(
            session,
            job,
            "job.stage",
            {"stage": "queued", "status": "queued"},
        )
    session.commit()
    session.refresh(job)
    return job, True


def _job_lock(session: Session, job_id: uuid.UUID) -> JobLock | None:
    """查询任务持有的锁；参数 session/job_id 为任务上下文。"""

    return session.scalar(
        select(JobLock).where(
            JobLock.job_id == job_id,
            JobLock.lock_name == "global_write",
        )
    )


def _scope_display(
    session: Session,
    job: Job,
    scopes: list[JobFundScope],
) -> JobScopeDisplayData:
    """构造任务范围名称；参数 session/job/scopes 为任务与冻结范围。"""

    definition = job.scope_definition or {}
    mode = str(definition.get("mode") or "all")
    if mode == "funds":
        names = list(dict.fromkeys(item.fund_name_snapshot for item in scopes))
        codes = list(dict.fromkeys(item.fund_code_snapshot for item in scopes))
        if len(names) == 1:
            return JobScopeDisplayData(
                mode=mode,
                primary=names[0],
                secondary=codes[0],
            )
        primary = "、".join(names[:2])
        if len(names) > 2:
            primary += f" 等 {len(names)} 只基金"
        secondary = "、".join(codes[:3])
        if len(codes) > 3:
            secondary += f" 等 {len(codes)} 个代码"
        return JobScopeDisplayData(
            mode=mode,
            primary=primary or "指定基金",
            secondary=secondary or None,
        )

    if mode == "manager":
        manager_ids: list[uuid.UUID] = []
        for value in definition.get("manager_ids") or []:
            try:
                manager_ids.append(uuid.UUID(str(value)))
            except (ValueError, TypeError):
                continue
        managers = list(
            session.scalars(
                select(Manager)
                .where(Manager.id.in_(manager_ids))
                .order_by(Manager.name_cn)
            )
        )
        short_names = [item.short_name or item.name_cn for item in managers]
        full_names = [item.name_cn for item in managers]
        return JobScopeDisplayData(
            mode=mode,
            primary="、".join(short_names) or "按管理人",
            secondary=(
                f"管理人 · {len(scopes)} 只基金"
                if len(short_names) <= 1
                else f"{'、'.join(full_names)} · {len(scopes)} 只基金"
            ),
        )

    if mode == "category":
        categories = [
            str(value)
            for value in definition.get("categories") or []
            if str(value).strip()
        ]
        return JobScopeDisplayData(
            mode=mode,
            primary="、".join(categories) or "按资产类别",
            secondary=f"资产类别 · {len(scopes)} 只基金",
        )

    if mode == "tag":
        tags = [
            str(value)
            for value in definition.get("tag_filters") or []
            if str(value).strip()
        ]
        return JobScopeDisplayData(
            mode=mode,
            primary="、".join(tags) or "按标签",
            secondary=f"标签范围 · {len(scopes)} 只基金",
        )

    if mode == "collection":
        collection_id = str(definition.get("collection_id") or "")
        return JobScopeDisplayData(
            mode=mode,
            primary="指定基金集合",
            secondary=f"{collection_id[:8]} · {len(scopes)} 只基金" if collection_id else f"{len(scopes)} 只基金",
        )

    return JobScopeDisplayData(
        mode=mode,
        primary="全量基金",
        secondary=f"{len(scopes)} 只基金",
    )


def _stage_runtime_progress(
    session: Session,
    job: Job,
) -> tuple[int, int] | None:
    """读取当前阶段最新事件进度；参数 session/job 为任务上下文。"""

    # 综合资料库由旧爬虫输出自己的基金总数，通常与当前数据库快照的
    # scope_count 不同（例如 HKEX 新快照新增基金）。优先读取下载阶段
    # 的结构化进度，即使任务已经进入校验/发布阶段也继续展示该进度。
    event_types = {"job.stage"}
    if job.task_target == "master_database":
        event_types.add("job.publish")
    events = session.scalars(
        select(JobEvent)
        .where(
            JobEvent.job_id == job.id,
            JobEvent.event_type.in_(event_types),
        )
        .order_by(JobEvent.sequence_no.desc())
        .limit(50)
    )
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if job.task_target == "master_database":
            if event.event_type == "job.publish" and payload.get("status") == "master_imported":
                summary = payload.get("summary")
                if isinstance(summary, dict):
                    try:
                        total = int(summary["funds"])
                    except (KeyError, TypeError, ValueError):
                        total = 0
                    if total > 0:
                        return total, total
            if event.event_type != "job.stage":
                continue
            if payload.get("stage") != "download":
                continue
            if payload.get("status") not in {"running", "completed"}:
                continue
        elif not job.current_stage:
            return None
        elif (
            payload.get("stage") != job.current_stage
            or payload.get("status") != "running"
        ):
            continue
        try:
            processed = int(payload["processed"])
            total = int(payload["total"])
        except (KeyError, TypeError, ValueError):
            continue
        if total > 0:
            return max(0, min(processed, total)), total
    return None


def build_job_data(session: Session, job: Job) -> JobData:
    """把任务 ORM 转为 API 数据；参数 session/job 为任务上下文。"""

    scopes = list(
        session.scalars(
            select(JobFundScope).where(JobFundScope.job_id == job.id)
        )
    )
    steps = list(
        session.scalars(
            select(JobStep)
            .where(JobStep.job_id == job.id)
            .order_by(JobStep.created_at, JobStep.step_type)
        )
    )
    processed = sum(
        1 for scope in scopes if scope.result_status not in {"pending", "running"}
    )
    succeeded = sum(1 for scope in scopes if scope.result_status == "success")
    skipped = sum(1 for scope in scopes if scope.result_status == "skipped")
    failed = sum(1 for scope in scopes if scope.result_status == "failed")
    runtime_progress = _stage_runtime_progress(session, job)
    default_total = 0 if job.task_target == "master_database" else len(scopes)
    lock = _job_lock(session, job.id)
    return JobData(
        job_id=job.id,
        batch_id=job.batch_id,
        base_batch_id=job.base_batch_id,
        status=job.status,
        outcome=job.outcome,
        scope_count=len(scopes),
        scope_definition=job.scope_definition or {},
        model_policy=(job.scope_definition or {}).get("model_policy", {}),
        scope_display=_scope_display(session, job, scopes),
        requested_stages=list(job.requested_stages or []),
        scope_hash=job.scope_hash,
        current_stage=job.current_stage,
        cancellation_requested=job.cancellation_requested,
        progress=JobProgress(
            total=runtime_progress[1] if runtime_progress else default_total,
            processed=runtime_progress[0] if runtime_progress else processed,
            succeeded=succeeded,
            skipped=skipped,
            failed=failed,
        ),
        steps=[
            JobStepData(
                step_type=step.step_type,
                status=step.status,
                total=step.total_count,
                succeeded=step.success_count,
                skipped=step.skip_count,
                failed=step.failure_count,
            )
            for step in steps
        ],
        lock=JobLockData(
            acquired=lock is not None,
            owner=lock.owner if lock else None,
            expires_at=lock.expires_at.astimezone(timezone.utc) if lock else None,
        ),
        created_at=job.created_at.astimezone(timezone.utc),
        started_at=(
            job.started_at.astimezone(timezone.utc) if job.started_at else None
        ),
        finished_at=(
            job.finished_at.astimezone(timezone.utc) if job.finished_at else None
        ),
        error_code=job.error_code,
        error_message=job.error_message,
        manifest_path=job.result_manifest_path,
        task_target=job.task_target or (job.scope_definition or {}).get("task_target", "fund_monthly"),
    )


def get_job(session: Session, job_id: uuid.UUID) -> JobData:
    """查询单个任务；参数 session/job_id 为查询目标。"""

    job = session.get(Job, job_id)
    if job is None:
        raise ApiError(404, "E_JOB_NOT_FOUND", "任务不存在")
    return build_job_data(session, job)


def list_jobs(
    session: Session,
    *,
    status: str | None,
    page: int,
    page_size: int,
) -> JobPageData:
    """分页查询任务；参数 session/status/page/page_size 为查询条件。"""

    statement = select(Job)
    count_statement = select(func.count()).select_from(Job)
    if status:
        statement = statement.where(Job.status == status)
        count_statement = count_statement.where(Job.status == status)
    total = int(session.scalar(count_statement) or 0)
    jobs = list(
        session.scalars(
            statement.order_by(Job.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return JobPageData(
        items=[build_job_data(session, job) for job in jobs],
        page=page,
        page_size=page_size,
        total=total,
    )


def list_job_funds(
    session: Session,
    job_id: uuid.UUID,
    *,
    result_statuses: list[str] | None,
    page: int,
    page_size: int,
) -> JobFundScopePageData:
    """查询任务基金结果；参数 job_id/statuses/page/page_size 为过滤条件。"""

    job = session.get(Job, job_id)
    if job is None:
        raise ApiError(404, "E_JOB_NOT_FOUND", "任务不存在")
    statement = select(JobFundScope).where(JobFundScope.job_id == job_id)
    count_statement = (
        select(func.count())
        .select_from(JobFundScope)
        .where(JobFundScope.job_id == job_id)
    )
    if result_statuses:
        statement = statement.where(
            JobFundScope.result_status.in_(result_statuses)
        )
        count_statement = count_statement.where(
            JobFundScope.result_status.in_(result_statuses)
        )
    total = int(session.scalar(count_statement) or 0)
    scopes = list(
        session.scalars(
            statement.order_by(JobFundScope.fund_code_snapshot)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return JobFundScopePageData(
        items=[
            JobFundScopeItemData(
                fund_id=item.fund_id,
                fund_code=item.fund_code_snapshot,
                fund_name=item.fund_name_snapshot,
                result_status=item.result_status,
                skip_reason=item.skip_reason,
            )
            for item in scopes
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


def list_job_fund_issues(
    session: Session,
    job_id: uuid.UUID,
) -> JobFundIssuePageData:
    """聚合任务各阶段的跳过与失败基金；参数 session/job_id 为查询上下文。"""

    job = session.get(Job, job_id)
    if job is None:
        raise ApiError(404, "E_JOB_NOT_FOUND", "任务不存在")
    scopes = list(
        session.scalars(
            select(JobFundScope)
            .where(JobFundScope.job_id == job_id)
            .order_by(JobFundScope.fund_code_snapshot)
        )
    )
    by_code = {item.fund_code_snapshot: item for item in scopes}
    latest: dict[tuple[str, str], dict] = {}
    resume_sequence = int(
        session.scalar(
            select(func.max(JobEvent.sequence_no)).where(
                JobEvent.job_id == job_id,
                JobEvent.event_type == "job.resume",
            )
        )
        or 0
    )
    events = list(
        session.scalars(
            select(JobEvent)
            .where(
                JobEvent.job_id == job_id,
                JobEvent.event_type == "job.fund",
                JobEvent.sequence_no > resume_sequence,
            )
            .order_by(JobEvent.sequence_no)
        )
    )
    for event in events:
        payload = event.payload or {}
        fund_code = str(payload.get("fund_code") or "").strip()
        if not fund_code:
            continue
        stage = str(payload.get("stage") or "download")
        key = (stage, fund_code)
        status = str(payload.get("status") or "").strip()
        if status not in {"skipped", "failed"}:
            latest.pop(key, None)
            continue
        scope = by_code.get(fund_code)
        latest[key] = {
            "fund_id": scope.fund_id if scope else None,
            "fund_code": fund_code,
            "fund_name": (
                str(payload.get("fund_name") or "").strip()
                or (scope.fund_name_snapshot if scope else fund_code)
            ),
            "stage": stage,
            "result_status": status,
            "reason": str(payload.get("reason") or "").strip()
            or ("任务已跳过" if status == "skipped" else "任务执行失败"),
        }

    steps = list(
        session.scalars(
            select(JobStep)
            .where(JobStep.job_id == job_id)
            .order_by(JobStep.created_at)
        )
    )
    result_steps = [
        step
        for step in steps
        if step.success_count or step.skip_count or step.failure_count
    ]
    fallback_stage = (
        result_steps[-1].step_type
        if result_steps
        else (steps[-1].step_type if steps else "download")
    )
    for scope in scopes:
        key = (fallback_stage, scope.fund_code_snapshot)
        if (
            scope.result_status in {"skipped", "failed"}
            and key not in latest
        ):
            latest[key] = {
                "fund_id": scope.fund_id,
                "fund_code": scope.fund_code_snapshot,
                "fund_name": scope.fund_name_snapshot,
                "stage": fallback_stage,
                "result_status": scope.result_status,
                "reason": scope.skip_reason or "未提供具体原因",
            }

    stage_order = {"download": 0, "parse": 1, "structure": 2}
    items = [
        JobFundIssueData(**value)
        for value in sorted(
            latest.values(),
            key=lambda item: (
                stage_order.get(item["stage"], 99),
                item["result_status"],
                item["fund_code"],
            ),
        )
        if value["fund_id"] is not None
    ]
    return JobFundIssuePageData(
        items=items,
        total=len(items),
        skipped=sum(item.result_status == "skipped" for item in items),
        failed=sum(item.result_status == "failed" for item in items),
    )


def cancel_job(session: Session, job_id: uuid.UUID) -> JobData:
    """请求取消任务；参数 session/job_id 为取消目标。"""

    job = session.get(Job, job_id)
    if job is None:
        raise ApiError(404, "E_JOB_NOT_FOUND", "任务不存在")
    if job.status == "cancelled":
        return build_job_data(session, job)
    if job.status in TERMINAL_JOB_STATUSES:
        raise ApiError(
            409,
            "E_JOB_STATE_CONFLICT",
            f"任务状态 {job.status} 不允许取消",
        )
    job.cancellation_requested = True
    if job.status in {"waiting_lock", "queued"}:
        session.execute(delete(JobLock).where(JobLock.job_id == job.id))
        job.status = "cancelled"
        job.finished_at = _utc_now()
        for step in session.scalars(
            select(JobStep).where(JobStep.job_id == job.id)
        ):
            if step.status == "pending":
                step.status = "cancelled"
        if job.batch_id:
            batch = session.get(DatasetBatch, job.batch_id)
            if batch and batch.status == "staging":
                batch.status = "failed"
        append_event(
            session,
            job,
            "job.completed",
            {"status": "cancelled", "reason": "cancel_requested"},
        )
    else:
        append_event(
            session,
            job,
            "job.warning",
            {"status": "cancel_requested"},
        )
    session.commit()
    session.refresh(job)
    return build_job_data(session, job)


def retry_job(
    session: Session,
    job_id: uuid.UUID,
    *,
    owner: str = "api",
) -> JobData:
    """从失败阶段继续任务；参数 session/job_id/owner 为任务与锁持有者。"""

    job = session.get(Job, job_id)
    if job is None:
        raise ApiError(404, "E_JOB_NOT_FOUND", "任务不存在")
    if job.status not in {"failed", "publish_failed"}:
        raise ApiError(
            409,
            "E_JOB_STATE_CONFLICT",
            f"任务状态 {job.status} 不允许断点续跑",
        )
    steps = list(
        session.scalars(
            select(JobStep)
            .where(JobStep.job_id == job.id)
            .order_by(JobStep.created_at, JobStep.step_type)
        )
    )
    retry_stages = []
    supplementary = (
        (job.scope_definition or {})
        .get("document_policy", {})
        .get("supplementary_source")
    )
    # URL 补充来源不是不可变输入：站点可能在任务失败后更新同一链接的
    # PDF。断点续跑时重新执行下载→解析→结构化，下载阶段会用 SHA 决定
    # 是否为新版本；上传文件则保持原有的“从失败阶段续跑”语义。
    refresh_url_source = (
        isinstance(supplementary, dict)
        and supplementary.get("source_kind") == "url"
        and bool(str(supplementary.get("source_url") or "").strip())
    )
    for step in steps:
        if step.status == "completed" and not refresh_url_source:
            continue
        step.status = "pending"
        step.success_count = 0
        step.skip_count = 0
        step.failure_count = 0
        retry_stages.append(step.step_type)
    if refresh_url_source:
        retry_stages = list(job.requested_stages or retry_stages)
    job.status = "waiting_lock"
    job.outcome = None
    job.worker_id = None
    job.cancellation_requested = False
    job.error_code = None
    job.error_message = None
    job.current_stage = None
    job.started_at = None
    job.finished_at = None
    job.result_manifest_path = None
    if job.batch_id:
        batch = session.get(DatasetBatch, job.batch_id)
        if batch and batch.status in {"failed", "publish_failed"}:
            batch.status = "staging"
    append_event(
        session,
        job,
        "job.resume",
        {
            "status": "waiting_lock",
            "stages": retry_stages or list(job.requested_stages or []),
        },
    )
    if _acquire_lock(session, job, owner):
        job.status = "queued"
        append_event(
            session,
            job,
            "job.stage",
            {
                "stage": "queued",
                "status": "queued",
                "resume": True,
            },
        )
    session.commit()
    session.refresh(job)
    return build_job_data(session, job)


def list_job_events(
    session: Session,
    job_id: uuid.UUID,
    *,
    after_sequence: int = 0,
    limit: int = 200,
    latest: bool = False,
) -> JobEventPageData:
    """查询任务事件；参数 session/job_id/after_sequence/limit/latest 为过滤和排序条件。"""

    job = session.get(Job, job_id)
    if job is None:
        raise ApiError(404, "E_JOB_NOT_FOUND", "任务不存在")
    statement = select(JobEvent).where(
        JobEvent.job_id == job_id,
        JobEvent.sequence_no > after_sequence,
    )
    statement = statement.order_by(
        desc(JobEvent.sequence_no) if latest else JobEvent.sequence_no
    ).limit(limit)
    events = list(session.scalars(statement))
    if latest:
        events.reverse()
    items = [
        JobEventData(
            event_id=event.id,
            job_id=event.job_id,
            sequence_no=event.sequence_no,
            type=event.event_type,
            occurred_at=event.created_at.astimezone(timezone.utc),
            snapshot_id=job.batch_id,
            payload=event.payload,
        )
        for event in events
    ]
    return JobEventPageData(
        items=items,
        last_sequence_no=items[-1].sequence_no if items else after_sequence,
    )


def job_event_stream_data(session: Session, job_id: uuid.UUID) -> list[JobEventData]:
    """读取 SSE 需要的全部事件；参数 session/job_id 为任务上下文。"""

    return list_job_events(session, job_id, limit=1000).items

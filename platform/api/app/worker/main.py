"""Single-writer D3 worker process."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import httpx
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import threading
import time
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.config import get_settings, logical_data_path
from app.db.session import create_session_factory
from app.models import (
    DatasetBatch,
    Job,
    JobAttempt,
    JobFundScope,
    JobLock,
    JobStep,
)
from app.services.jobs import append_event
from app.worker.adapters import (
    AdapterResult,
    run_download_stage,
    run_parse_stage,
    run_structure_stage,
)


logger = logging.getLogger(__name__)

ORPHAN_RECOVERY_INTERVAL_SECONDS = 30.0
ACTIVE_JOB_STATUSES = ("running", "retrying")


def utc_now() -> datetime:
    """返回 UTC 当前时间；参数为空。"""

    return datetime.now(timezone.utc)


def link_or_copy_tree(source: Path, target: Path) -> None:
    """用硬链接复用目录文件，跨卷时回退复制；参数 source/target 为目录。"""

    target.mkdir(parents=True, exist_ok=True)
    for current_root, directory_names, file_names in os.walk(source):
        current = Path(current_root)
        relative = current.relative_to(source)
        destination_dir = target / relative
        destination_dir.mkdir(parents=True, exist_ok=True)
        for directory_name in directory_names:
            (destination_dir / directory_name).mkdir(exist_ok=True)
        for file_name in file_names:
            source_file = current / file_name
            target_file = destination_dir / file_name
            if target_file.exists():
                target_file.unlink()
            try:
                os.link(source_file, target_file)
            except OSError:
                shutil.copy2(source_file, target_file)


def artifact_name_key(value: str) -> str:
    """生成产物文件名匹配键；参数 value 为基金名或 PDF 文件名。"""

    return "".join(
        character
        for character in Path(str(value or "")).stem.casefold()
        if character.isalnum()
    )


def link_or_copy_file(source: Path, target: Path) -> None:
    """复用单个产物文件；参数 source/target 为源文件和目标文件。"""

    target.parent.mkdir(parents=True, exist_ok=True)
    # 断点续跑可能已经留下了同一源文件的硬链接。此时再次尝试
    # ``os.link`` 会得到 ``FileExistsError``，回退到 ``copy2`` 又会因为
    # 源/目标是同一 inode 抛出 ``SameFileError``。幂等复用即可直接返回；
    # 若目标是旧文件，则先移除再重新建立硬链接/副本。
    if target.exists():
        try:
            if target.samefile(source):
                return
        except OSError:
            pass
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


class Worker:
    """Claim and execute one queued job at a time."""

    def __init__(self, *, worker_id: str | None = None, poll_interval: float = 1.0):
        """初始化 Worker。

        参数 worker_id：实例标识；poll_interval：轮询间隔秒数。
        """

        self.settings = get_settings()
        self.factory = create_session_factory()
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}"
        self.poll_interval = poll_interval
        self._last_recovery = 0.0

    def recover_orphaned_jobs(self) -> int:
        """把租约已过期且无有效锁的活动任务标记失败，避免永久卡在 running。"""

        now = utc_now()
        heartbeat_cutoff = now - timedelta(minutes=10)
        recovered = 0
        with self.factory() as session:
            has_live_lock = (
                select(JobLock.id)
                .where(
                    JobLock.job_id == Job.id,
                    JobLock.expires_at > now,
                )
                .exists()
            )
            jobs = list(
                session.scalars(
                    select(Job)
                    .where(
                        Job.status.in_(ACTIVE_JOB_STATUSES),
                        ~has_live_lock,
                        (Job.last_heartbeat_at.is_(None))
                        | (Job.last_heartbeat_at <= heartbeat_cutoff),
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            for job in jobs:
                stage = job.current_stage or "unknown"
                job.status = "failed"
                job.outcome = "partial"
                job.error_code = "E_WORKER_LOST"
                job.error_message = f"Worker heartbeat expired during {stage}"
                job.finished_at = now
                job.current_stage = None
                if job.batch_id:
                    batch = session.get(DatasetBatch, job.batch_id)
                    if batch is not None and batch.status == "staging":
                        batch.status = "failed"
                session.execute(
                    delete(JobLock).where(
                        JobLock.job_id == job.id,
                        JobLock.expires_at <= now,
                    )
                )
                append_event(
                    session,
                    job,
                    "job.recovered",
                    {
                        "status": "failed",
                        "reason": "worker_heartbeat_expired",
                        "stage": stage,
                    },
                )
                recovered += 1
            session.commit()
        if recovered:
            logger.warning(
                "Recovered %s orphaned job(s) with expired worker leases",
                recovered,
            )
        return recovered

    def _recover_if_due(self) -> None:
        """按固定间隔回收孤儿任务，避免每次轮询都扫描活动任务。"""

        now = time.monotonic()
        if now - self._last_recovery < ORPHAN_RECOVERY_INTERVAL_SECONDS:
            return
        self._last_recovery = now
        self.recover_orphaned_jobs()

    def emit_event(self, job_id: uuid.UUID, event_type: str, payload: dict) -> None:
        """独立事务写入任务事件；参数 job_id/type/payload 为事件内容。"""

        with self.factory() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            append_event(session, job, event_type, payload)
            session.commit()

    def promote_waiting(self) -> None:
        """在没有有效锁时把一个等待任务提升为 queued。"""

        with self.factory() as session:
            lock = session.scalar(
                select(JobLock)
                .where(JobLock.lock_name == "global_write")
                .with_for_update()
            )
            now = utc_now()
            active_compute = session.scalar(
                select(Job.id)
                .where(
                    Job.status.in_({"running", "validating", "publishing"}),
                    Job.last_heartbeat_at.is_not(None),
                    Job.last_heartbeat_at > now - timedelta(minutes=10),
                )
                .limit(1)
            )
            if active_compute is not None and lock is None:
                session.commit()
                return
            if lock and lock.expires_at > now:
                return
            if lock:
                session.delete(lock)
                session.flush()
            job = session.scalar(
                select(Job)
                .where(Job.status == "waiting_lock")
                .order_by(Job.created_at)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                session.commit()
                return
            inserted = session.execute(
                pg_insert(JobLock)
                .values(
                    id=uuid.uuid4(),
                    job_id=job.id,
                    lock_name="global_write",
                    owner=self.worker_id,
                    heartbeat_at=now,
                    expires_at=now + timedelta(minutes=10),
                )
                .on_conflict_do_nothing(index_elements=[JobLock.lock_name])
                .returning(JobLock.id)
            ).scalar_one_or_none()
            if inserted:
                job.status = "queued"
                append_event(
                    session,
                    job,
                    "job.stage",
                    {"stage": "queued", "status": "queued"},
                )
            session.commit()

    def claim_next(self) -> Job | None:
        """领取下一个 queued 任务；参数为空。"""

        self.promote_waiting()
        with self.factory() as session:
            job = session.scalar(
                select(Job)
                .where(Job.status == "queued")
                .order_by(Job.created_at)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                session.rollback()
                return None
            lock = session.scalar(
                select(JobLock).where(
                    JobLock.lock_name == "global_write",
                    JobLock.job_id == job.id,
                )
            )
            if lock is None:
                session.rollback()
                return None
            lock.owner = self.worker_id
            lock.lease_token = uuid.uuid4().hex
            lock.heartbeat_at = utc_now()
            lock.expires_at = utc_now() + timedelta(minutes=10)
            job.status = "running"
            job.worker_id = self.worker_id
            job.last_heartbeat_at = utc_now()
            job.started_at = job.started_at or utc_now()
            job.current_stage = (job.requested_stages or [None])[0]
            session.commit()
            session.refresh(job)
            session.expunge(job)
            job._lease_token = lock.lease_token  # type: ignore[attr-defined]
            return job

    def _monitor(
        self,
        job_id: uuid.UUID,
        cancel_event: threading.Event,
        stop_event: threading.Event,
        lease_ref: dict[str, str | None] | None = None,
    ) -> None:
        """续约租约并观察取消请求；参数 job_id/cancel/stop 为监控上下文。"""

        lease_ref = lease_ref if lease_ref is not None else {"token": None}
        while not stop_event.wait(1.0):
            try:
                with self.factory() as session:
                    job = session.get(Job, job_id)
                    lock = session.scalar(
                        select(JobLock).where(
                            JobLock.job_id == job_id,
                            JobLock.lock_name == "global_write",
                        )
                    )
                    now = utc_now()
                    if job is None:
                        cancel_event.set()
                        return
                    job.last_heartbeat_at = now
                    token = lease_ref.get("token")
                    if lock is None:
                        if token is not None:
                            cancel_event.set()
                            return
                        session.commit()
                        continue
                    if job.cancellation_requested:
                        cancel_event.set()
                    if token is not None:
                        if lock.lease_token != token or lock.owner != self.worker_id:
                            cancel_event.set()
                            return
                        lock.heartbeat_at = now
                        lock.expires_at = now + timedelta(minutes=10)
                    session.commit()
            except (OperationalError, DBAPIError) as exc:
                logger.warning(
                    "Worker heartbeat failed; will retry: %s",
                    exc,
                )
            except StaleDataError:
                # 发布事务会在成功后删除租约；监控线程可能恰好拿到
                # 已被发布事务删除的 ORM 行，视为正常收尾而非线程崩溃。
                cancel_event.set()
                return

    def _copy_input_job_artifacts(
        self,
        job: Job,
        job_root: Path,
        *,
        session: Session | None = None,
    ) -> None:
        """复制前序任务产物到当前任务；参数 job/job_root 为续跑上下文。"""

        input_job_id = str(
            (job.scope_definition or {}).get("input_job_id") or ""
        ).strip()
        if not input_job_id:
            return
        input_uuid = uuid.UUID(input_job_id)
        source_root = (
            self.settings.data_root
            / "jobs"
            / str(input_uuid)
            / "staging"
            / str(input_uuid)
        )
        target_root = job_root / "staging" / str(job.id)
        if not source_root.is_dir():
            raise RuntimeError(f"前序任务产物不存在：{source_root}")
        stages = set(job.requested_stages or [])
        artifact_names = ["factsheet"]
        if "structure" in stages and "parse" not in stages:
            artifact_names.append("markdown")
            # 已完成结构化的前序任务可直接复用逐 PDF 结果，避免重跑本地模型。
            # 目标任务仍会按冻结基金范围导入，额外的合并文件不会越过发布范围。
            if (source_root / "structure_data").is_dir():
                artifact_names.append("structure_data")
        target_root.mkdir(parents=True, exist_ok=True)

        # 续跑子集时只复制当前任务冻结的基金文件，避免下游脚本重新处理
        # 前序任务中的全集。没有 session 的旧调用保留完整复制行为。
        selected_keys: set[str] | None = None
        if session is not None:
            scopes = list(
                session.scalars(
                    select(JobFundScope).where(JobFundScope.job_id == job.id)
                )
            )
            selected_keys = {
                artifact_name_key(scope.fund_name_snapshot)
                for scope in scopes
            }
        for artifact_name in artifact_names:
            source = source_root / artifact_name
            if not source.is_dir():
                raise RuntimeError(f"前序任务缺少必要产物：{source}")
            target = target_root / artifact_name
            if selected_keys is None:
                link_or_copy_tree(source, target)
                continue
            target.mkdir(parents=True, exist_ok=True)
            if artifact_name == "factsheet":
                for source_file in source.rglob("*.pdf"):
                    if artifact_name_key(source_file.name) in selected_keys:
                        link_or_copy_file(
                            source_file,
                            target / source_file.relative_to(source),
                        )
                continue
            if artifact_name == "structure_data":
                link_or_copy_tree(source, target)
                continue
            for manifest in source.rglob("manifest.json"):
                try:
                    payload = json.loads(manifest.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                source_pdf = str(payload.get("source_pdf") or "")
                if artifact_name_key(source_pdf) not in selected_keys:
                    continue
                link_or_copy_tree(
                    manifest.parent,
                    target / manifest.parent.relative_to(source),
                )

    def _build_and_submit_manifest(
        self,
        job: Job,
        job_root: Path,
    ) -> str:
        """构建 manifest 并提交 API 发布；参数 job/job_root 为任务。"""

        if job.batch_id is None or job.base_batch_id is None:
            raise RuntimeError("任务缺少 batch_id 或 base_batch_id")
        staging = job_root / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        files = []
        for path in sorted(staging.rglob("*")):
            if not path.is_file():
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            files.append(
                {
                    "path": path.relative_to(job_root).as_posix(),
                    "sha256": digest,
                    "size_bytes": path.stat().st_size,
                }
            )
        manifest = {
            "schema_version": "1.0",
            "batch_id": str(job.batch_id),
            "job_id": str(job.id),
            "base_batch_id": str(job.base_batch_id),
            "worker_id": self.worker_id,
            "lease_token": str(getattr(job, "_lease_token", "")),
            "stages": job.requested_stages,
            "files": files,
            "created_at": utc_now().isoformat(),
        }
        manifest_path = job_root / "staging_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        api_url = os.environ.get("PLATFORM_API_URL", "http://127.0.0.1:8000")
        response = httpx.post(
            f"{api_url}/api/jobs/{job.id}/publish",
            json={
                "manifest_path": logical_data_path(
                    manifest_path, self.settings.data_root
                ).as_posix(),
                "manifest_sha256": manifest_sha256,
                "lease_token": str(getattr(job, "_lease_token", "")),
            },
            timeout=120,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"API publish failed ({response.status_code}): {response.text[:1000]}"
            )
        return str(response.json()["data"]["manifest_path"])

    def _release_compute_lease(
        self,
        job_id: uuid.UUID,
        lease_ref: dict[str, str | None],
    ) -> None:
        """释放长耗时计算阶段的全局写锁；参数 job_id/lease_ref 为当前执行。"""

        token = lease_ref.get("token")
        if not token:
            return
        with self.factory() as session:
            session.execute(
                delete(JobLock).where(
                    JobLock.job_id == job_id,
                    JobLock.owner == self.worker_id,
                    JobLock.lease_token == token,
                )
            )
            session.commit()
        lease_ref["token"] = None

    def _acquire_publish_lease(
        self,
        job_id: uuid.UUID,
        lease_ref: dict[str, str | None],
        cancel_event: threading.Event,
    ) -> None:
        """在发布前取得短期全局写锁；参数为任务、令牌引用和取消事件。"""

        while not cancel_event.is_set():
            now = utc_now()
            token = uuid.uuid4().hex
            with self.factory() as session:
                session.execute(delete(JobLock).where(JobLock.expires_at <= now))
                inserted = session.execute(
                    pg_insert(JobLock)
                    .values(
                        id=uuid.uuid4(),
                        job_id=job_id,
                        lock_name="global_write",
                        owner=self.worker_id,
                        lease_token=token,
                        heartbeat_at=now,
                        expires_at=now + timedelta(minutes=10),
                    )
                    .on_conflict_do_nothing(index_elements=[JobLock.lock_name])
                    .returning(JobLock.id)
                ).scalar_one_or_none()
                session.commit()
            if inserted:
                lease_ref["token"] = token
                return
            time.sleep(0.5)
        raise InterruptedError("publish lease acquisition cancelled")

    def _update_step(
        self,
        session: Session,
        job: Job,
        stage: str,
        status: str,
    ) -> None:
        """根据基金范围结果更新阶段计数；参数 job/stage/status。"""

        step = session.scalar(
            select(JobStep).where(
                JobStep.job_id == job.id,
                JobStep.step_type == stage,
            )
        )
        scopes = list(
            session.scalars(
                select(JobFundScope).where(JobFundScope.job_id == job.id)
            )
        )
        if step:
            step.status = status
            step.total_count = len(scopes)
            step.success_count = sum(
                1 for item in scopes if item.result_status == "success"
            )
            step.skip_count = sum(
                1 for item in scopes if item.result_status == "skipped"
            )
            step.failure_count = sum(
                1 for item in scopes if item.result_status == "failed"
            )
            if status == "running":
                attempt = session.scalar(
                    select(JobAttempt)
                    .where(
                        JobAttempt.job_step_id == step.id,
                        JobAttempt.finished_at.is_(None),
                    )
                    .order_by(JobAttempt.attempt_no.desc())
                )
                if attempt is None:
                    latest = session.scalar(
                        select(JobAttempt.attempt_no)
                        .where(JobAttempt.job_step_id == step.id)
                        .order_by(JobAttempt.attempt_no.desc())
                        .limit(1)
                    )
                    session.add(
                        JobAttempt(
                            id=uuid.uuid4(),
                            job_step_id=step.id,
                            attempt_no=int(latest or 0) + 1,
                            started_at=utc_now(),
                        )
                    )
            else:
                attempt = session.scalar(
                    select(JobAttempt)
                    .where(
                        JobAttempt.job_step_id == step.id,
                        JobAttempt.finished_at.is_(None),
                    )
                    .order_by(JobAttempt.attempt_no.desc())
                )
            if status != "running" and attempt is not None:
                attempt.finished_at = utc_now()
                if status in {"failed", "cancelled"}:
                    attempt.error_type = "task_execution"
                attempt.error_code = (
                    "E_TASK_CANCELLED" if status == "cancelled" else "E_TASK_FAILED"
                )

    def _reset_stage_results(self, session: Session, job: Job) -> None:
        """重置当前阶段基金状态；参数 session/job 为任务上下文。"""

        for scope in session.scalars(
            select(JobFundScope).where(JobFundScope.job_id == job.id)
        ):
            scope.result_status = "pending"
            scope.skip_reason = None

    def _apply_stage_results(
        self,
        session: Session,
        job: Job,
        stage: str,
        result: AdapterResult,
    ) -> None:
        """记录 Adapter 的逐基金结果；参数 session/job/stage/result 为阶段上下文。"""

        scopes = list(
            session.scalars(
                select(JobFundScope).where(JobFundScope.job_id == job.id)
            )
        )
        by_code = {item.fund_code_snapshot: item for item in scopes}
        fund_results = result.details.get("fund_results") or []
        issue_results: list[dict] = []
        for item in fund_results:
            scope = by_code.get(str(item.get("fund_code") or ""))
            if scope is None:
                continue
            status = str(item.get("status") or "").strip()
            if status not in {"success", "skipped", "failed", "cancelled"}:
                continue
            scope.result_status = status
            scope.skip_reason = str(item.get("reason") or "").strip() or None
            if status in {"skipped", "failed"}:
                issue_results.append(item)
        if result.details.get("complete_fund_results"):
            for scope in scopes:
                if scope.result_status != "pending":
                    continue
                scope.result_status = (
                    "success" if result.status == "success" else "failed"
                )
                scope.skip_reason = (
                    None if result.status == "success" else result.message
                )
        for item in issue_results:
            scope = by_code[str(item["fund_code"])]
            append_event(
                session,
                job,
                "job.fund",
                {
                    "stage": stage,
                    "fund_code": scope.fund_code_snapshot,
                    "fund_name": scope.fund_name_snapshot,
                    "status": item["status"],
                    "reason": item.get("reason"),
                },
            )

    def process(self, job: Job) -> None:
        """执行一个已领取任务；参数 job：任务记录。"""

        job_root = self.settings.data_root / "jobs" / str(job.id)
        job_root.mkdir(parents=True, exist_ok=True)
        cancel_event = threading.Event()
        stop_monitor = threading.Event()
        lease_ref: dict[str, str | None] = {
            "token": getattr(job, "_lease_token", None),
        }
        monitor = threading.Thread(
            target=self._monitor,
            args=(job.id, cancel_event, stop_monitor, lease_ref),
            name=f"job-monitor-{job.id}",
            daemon=True,
        )
        monitor.start()
        final_status = "failed"
        try:
            with self.factory() as session:
                current_job = session.get(Job, job.id)
                if current_job is None:
                    return
                self._copy_input_job_artifacts(
                    current_job,
                    job_root,
                    session=session,
                )
                # 外部下载/解析/模型推理不需要占用数据库全局写锁；
                # 只在最终发布前重新取得写锁，避免长任务阻塞整个队列。
                self._release_compute_lease(job.id, lease_ref)
                job._lease_token = ""  # type: ignore[attr-defined]
                stages = list(current_job.requested_stages or [])
                completed_stages = set(
                    session.scalars(
                        select(JobStep.step_type).where(
                            JobStep.job_id == current_job.id,
                            JobStep.status == "completed",
                        )
                    )
                )
                for stage in stages:
                    if stage in completed_stages:
                        continue
                    if cancel_event.is_set():
                        raise InterruptedError("cancel requested")
                    self._reset_stage_results(session, current_job)
                    current_job.current_stage = stage
                    self._update_step(session, current_job, stage, "running")
                    append_event(
                        session,
                        current_job,
                        "job.stage",
                        {"stage": stage, "status": "running"},
                    )
                    session.commit()
                    if stage == "download":
                        result = run_download_stage(
                            session,
                            current_job,
                            job_root=job_root,
                            cancel_event=cancel_event,
                            event_callback=lambda event_type, payload: self.emit_event(
                                job.id,
                                event_type,
                                payload,
                            ),
                        )
                    elif stage == "parse":
                        result = run_parse_stage(
                            session,
                            current_job,
                            job_root=job_root,
                            cancel_event=cancel_event,
                            event_callback=lambda event_type, payload: self.emit_event(
                                job.id,
                                event_type,
                                payload,
                            ),
                        )
                    elif stage == "structure":
                        result = run_structure_stage(
                            session,
                            current_job,
                            job_root=job_root,
                            cancel_event=cancel_event,
                            event_callback=lambda event_type, payload: self.emit_event(
                                job.id,
                                event_type,
                                payload,
                            ),
                        )
                    else:
                        raise RuntimeError(f"未注册的 Adapter：{stage}")
                    self._apply_stage_results(
                        session,
                        current_job,
                        stage,
                        result,
                    )
                    pending_count = session.scalar(
                        select(func.count())
                        .select_from(JobFundScope)
                        .where(
                            JobFundScope.job_id == current_job.id,
                            JobFundScope.result_status == "pending",
                        )
                    )
                    if result.status == "success" and pending_count:
                        raise RuntimeError(
                            f"{stage} 阶段结果未覆盖 {pending_count} 只基金，拒绝发布不完整批次"
                        )
                    if result.status == "cancelled":
                        raise InterruptedError(result.message)
                    if result.status != "success":
                        raise RuntimeError(result.message)
                    self._update_step(session, current_job, stage, "completed")
                    append_event(
                        session,
                        current_job,
                        "job.stage",
                        {
                            "stage": stage,
                            "status": "completed",
                            **result.details,
                        },
                    )
                    session.commit()

                self._acquire_publish_lease(job.id, lease_ref, cancel_event)
                current_job._lease_token = lease_ref["token"]  # type: ignore[attr-defined]
                current_job.current_stage = "validating"
                current_job.status = "validating"
                append_event(
                    session,
                    current_job,
                    "job.stage",
                    {"stage": "validating", "status": "running"},
                )
                session.commit()
                manifest_path = self._build_and_submit_manifest(
                    current_job,
                    job_root,
                )
                final_status = "completed"
        except InterruptedError:
            with self.factory() as session:
                current_job = session.get(Job, job.id)
                if current_job:
                    if current_job.current_stage:
                        self._update_step(
                            session,
                            current_job,
                            current_job.current_stage,
                            "cancelled",
                        )
                    current_job.status = "cancelled"
                    current_job.outcome = "partial"
                    current_job.finished_at = utc_now()
                    current_job.current_stage = None
                    if current_job.batch_id:
                        batch = session.get(DatasetBatch, current_job.batch_id)
                        if batch and batch.status == "staging":
                            batch.status = "failed"
                    append_event(
                        session,
                        current_job,
                        "job.completed",
                        {"status": "cancelled"},
                    )
                    session.commit()
            final_status = "cancelled"
        except Exception as exc:
            with self.factory() as session:
                current_job = session.get(Job, job.id)
                if current_job:
                    if current_job.status not in {"completed", "cancelled"}:
                        if current_job.current_stage:
                            self._update_step(
                                session,
                                current_job,
                                current_job.current_stage,
                                "failed",
                            )
                        current_job.status = (
                            "publish_failed"
                            if current_job.status == "publishing"
                            else "failed"
                        )
                        current_job.error_code = "E_TASK_EXECUTION"
                        current_job.error_message = f"{type(exc).__name__}: {exc}"
                        current_job.finished_at = utc_now()
                        current_job.current_stage = None
                        if current_job.batch_id:
                            batch = session.get(DatasetBatch, current_job.batch_id)
                            if batch and batch.status == "staging":
                                batch.status = (
                                    "publish_failed"
                                    if current_job.status == "publish_failed"
                                    else "failed"
                                )
                        append_event(
                            session,
                            current_job,
                            "job.warning",
                            {
                                "status": current_job.status,
                                "message": current_job.error_message,
                            },
                        )
                        session.commit()
            final_status = "failed"
        finally:
            stop_monitor.set()
            monitor.join(timeout=3)
            with self.factory() as session:
                token = lease_ref.get("token")
                statement = delete(JobLock).where(
                    JobLock.job_id == job.id,
                    JobLock.owner == self.worker_id,
                )
                if token:
                    statement = statement.where(JobLock.lease_token == token)
                session.execute(statement)
                session.commit()
            # 不要在任务收尾时预先提升下一个 waiting_lock 任务。
            # ``--once`` Worker 会在这里立即退出，若先创建全局锁就会把
            # 下一个任务留下 queued + lock 的孤儿状态；持续 Worker 下一轮
            # ``claim_next`` 会自行调用 ``promote_waiting``，无需提前占锁。

    def run_once(self) -> bool:
        """处理至多一个任务；参数为空，返回是否执行。"""

        job = self.claim_next()
        if job is None:
            return False
        self.process(job)
        return True

    def run_forever(self) -> None:
        """持续轮询任务直至进程退出；参数为空。"""

        backoff = 1.0
        while True:
            try:
                self._recover_if_due()
                if not self.run_once():
                    time.sleep(self.poll_interval)
                backoff = 1.0
            except (OperationalError, DBAPIError) as exc:
                logger.warning(
                    "Worker database connection failed; retrying in %.1fs: %s",
                    backoff,
                    exc,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            except Exception:
                logger.exception("Worker loop failed; retrying")
                time.sleep(max(1.0, min(self.poll_interval, 5.0)))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析 Worker 参数；参数 argv 为可选参数列表。"""

    parser = argparse.ArgumentParser(description="D3 single-writer worker.")
    parser.add_argument("--once", action="store_true", help="处理至多一个任务后退出。")
    parser.add_argument("--worker-id", default="")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument(
        "--browser-preflight",
        action="store_true",
        help="启动前验证原生 Playwright 浏览器；正式服务必须开启。",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """启动 Worker；参数 argv 为可选参数列表。"""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = parse_args(argv)
    require_browser = args.browser_preflight or os.environ.get(
        "PLATFORM_BROWSER_PREFLIGHT", ""
    ).strip().lower() in {"1", "true", "yes", "required"}
    if require_browser:
        from app.worker.browser_preflight import check_browser

        try:
            result = check_browser()
        except RuntimeError as exc:
            logger.error("Native browser preflight failed: %s", exc)
            return 78
        logger.info(
            "Native browser preflight passed: %s (%s)",
            result.browser,
            result.version,
        )
    worker = Worker(
        worker_id=args.worker_id or None,
        poll_interval=args.poll_interval,
    )
    if args.once:
        worker.run_once()
        return 0
    worker.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""API-owned validation and atomic publication of worker manifests."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import shutil
import uuid
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.core.config import get_settings, logical_data_path, resolve_managed_path
from app.importers.service import D1ImportService
from app.models import (
    DatasetBatch,
    DocumentSubject,
    DocumentVersion,
    FundMetric,
    FundReportSource,
    Job,
    JobFundScope,
    JobLock,
    PublicationState,
)
from app.schemas.jobs import PublishResultData
from app.services.jobs import append_event
from app.services.master_database import config_from_job
from app.services.report_type import detect_report_kind, report_kind_label


def _clone_batch_results(
    session: Session,
    *,
    source_batch_id: uuid.UUID,
    target_batch_id: uuid.UUID,
) -> None:
    """克隆当前发布结果到新批次；参数 session/source/target 为批次。"""

    statements = (
        """
        INSERT INTO fund_metrics (
          id, batch_id, fund_id, document_version_id, report_period,
          metric_name, numeric_value, text_value, unit, confidence,
          review_status, source_page, metadata_json
        )
        SELECT gen_random_uuid(), :target, fund_id, document_version_id,
               report_period, metric_name, numeric_value, text_value, unit,
               confidence, review_status, source_page, metadata_json
        FROM fund_metrics WHERE batch_id = :source
        """,
        """
        INSERT INTO fund_holdings (
          id, batch_id, fund_id, document_version_id, report_period,
          security_name, ticker, holding_quantity, registration_place, holding_type,
          weight, market_value, source_page
        )
        SELECT gen_random_uuid(), :target, fund_id, document_version_id,
               report_period, security_name, ticker, holding_quantity, registration_place,
               holding_type, weight, market_value, source_page
        FROM fund_holdings WHERE batch_id = :source
        """,
        """
        INSERT INTO fund_allocations (
          id, batch_id, fund_id, document_version_id, report_period,
          dimension, category, weight, unit, source_page, metadata_json
        )
        SELECT gen_random_uuid(), :target, fund_id, document_version_id,
               report_period, dimension, category, weight, unit, source_page,
               metadata_json
        FROM fund_allocations WHERE batch_id = :source
        """,
        """
        INSERT INTO fund_scale_history (
          id, fund_id, source_batch_id, as_of_date, fund_size, currency,
          source_unit
        )
        SELECT gen_random_uuid(), fund_id, :target, as_of_date, fund_size,
               currency, source_unit
        FROM fund_scale_history
        WHERE source_batch_id = :source AND as_of_date <= CURRENT_DATE
        """,
        """
        INSERT INTO quality_issues (
          id, batch_id, fund_id, severity, issue_type, details, status,
          metadata_json
        )
        SELECT gen_random_uuid(), :target, fund_id, severity, issue_type,
               details, status, metadata_json
        FROM quality_issues WHERE batch_id = :source
        """,
    )
    for statement in statements:
        session.execute(
            text(statement),
            {"source": source_batch_id, "target": target_batch_id},
        )


def _safe_manifest_file(path: Path, data_root: Path) -> Path:
    """确认 manifest 位于受管数据目录且属于任务 staging。"""

    resolved = path.resolve()
    try:
        relative = resolved.relative_to(data_root.resolve())
    except ValueError as exc:
        raise ApiError(422, "E_PUBLISH_MANIFEST", "manifest 不在受管数据目录内") from exc
    if len(relative.parts) < 3 or relative.parts[0] != "jobs" or relative.parts[2] != "staging":
        raise ApiError(422, "E_PUBLISH_MANIFEST", "manifest 不在任务 staging 中")
    return resolved


def _publish_supplementary_source(
    session: Session,
    *,
    job: Job,
    batch: DatasetBatch,
    scopes: list[JobFundScope],
) -> None:
    """把补充来源任务的输入登记为基金详情中的证据卡片。"""

    policy = (job.scope_definition or {}).get("document_policy") or {}
    source = policy.get("supplementary_source")
    if not isinstance(source, dict) or len(scopes) != 1:
        return
    scope = scopes[0]
    # 下载阶段记录的 SHA 是本次任务的内容边界。优先按它选版本，避免同一
    # URL 的新半年报与旧月报报告期相同/缺失时被数据库排序选错。
    download_sha256 = str(source.get("download_sha256") or "").strip()
    version = None
    if download_sha256:
        version = session.scalar(
            select(DocumentVersion)
            .join(FundMetric, FundMetric.document_version_id == DocumentVersion.id)
            .where(
                FundMetric.batch_id == batch.id,
                FundMetric.fund_id == scope.fund_id,
                DocumentVersion.sha256 == download_sha256,
            )
            .order_by(DocumentVersion.report_date.desc().nullslast())
            .limit(1)
        )
    if version is None:
        version = session.scalar(
            select(DocumentVersion)
            .join(FundMetric, FundMetric.document_version_id == DocumentVersion.id)
            .where(
                FundMetric.batch_id == batch.id,
                FundMetric.fund_id == scope.fund_id,
            )
            .order_by(DocumentVersion.report_date.desc().nullslast())
            .limit(1)
        )
    if version is None:
        subject_query = select(DocumentVersion).join(
            DocumentSubject,
            DocumentSubject.document_version_id == DocumentVersion.id,
        ).where(
            DocumentSubject.fund_id == scope.fund_id,
        )
        if download_sha256:
            subject_query = subject_query.where(DocumentVersion.sha256 == download_sha256)
        version = session.scalar(
            subject_query.order_by(DocumentVersion.report_date.desc().nullslast()).limit(1)
        )
    report_date = version.report_date.isoformat() if version and version.report_date else None
    report_text = ""
    if version is not None:
        # MinerU's full.md is the most reliable and cheapest source for report
        # type detection.  It is already part of the verified task artifacts;
        # fall back to the PDF text layer when a parser did not emit Markdown.
        task_root = (
            get_settings().data_root
            / "batches"
            / str(batch.id)
            / "staging"
            / str(job.id)
        )
        markdown_candidates = sorted(
            task_root.glob("markdown/**/full.md")
        )
        if markdown_candidates:
            try:
                report_text = markdown_candidates[0].read_text(encoding="utf-8")
            except OSError:
                report_text = ""
        if not report_text and version.file_path:
            source_pdf = resolve_managed_path(get_settings(), version.file_path)
            if source_pdf.is_file():
                try:
                    from pypdf import PdfReader

                    report_text = "\n".join(
                        page.extract_text() or "" for page in PdfReader(str(source_pdf)).pages
                    )
                except Exception:  # noqa: BLE001
                    report_text = ""
    detected_kind = detect_report_kind(
        report_text,
        version.report_date if version else None,
        version.file_path if version else None,
    )
    report_kind = str(source.get("report_kind") or detected_kind)
    if report_kind not in {"monthly", "semiannual", "annual", "supplementary"}:
        report_kind = detected_kind
    source_label = report_kind_label(
        report_kind,
        version.report_date if version else None,
    )
    source_url = str(source.get("source_url") or "").strip() or None
    if not source_url and version and version.source_url:
        source_url = str(version.source_url)
    metadata = {
        "source_label": source_label,
        "report_kind": report_kind,
        "report_period": report_date,
        "document_version_id": str(version.id) if version else None,
        "source_sha256": version.sha256 if version else download_sha256 or None,
        "download_sha256": download_sha256 or None,
        "source_download_sha256": source.get("source_download_sha256"),
        "pdf_sliced": bool(source.get("pdf_sliced", False)),
        "source_pages": source.get("source_pages"),
        "matched_pages": source.get("matched_pages") or [],
        "selected_pages": source.get("selected_pages") or [],
        "match_method": source.get("match_method"),
        "matched_names": source.get("matched_names") or [],
        "previous_sha256": source.get("previous_sha256"),
        "content_changed": bool(source.get("content_changed", False)),
        "resolved_url": source.get("resolved_url"),
        "language": source.get("language") or "原文",
        "source_url": source_url,
        "fixed_candidate": bool(source.get("fixed_candidate", True)),
        "available_sections": ["原文", "结构化数据"],
        "job_id": str(job.id),
    }
    source_key = f"supplementary:{job.id}"
    existing = session.scalar(
        select(FundReportSource).where(FundReportSource.source_key == source_key)
    )
    if existing is None:
        existing = FundReportSource(
            fund_id=scope.fund_id,
            source_system="manual_supplementary",
            source_key=source_key,
            source_sheet="manual",
            source_row=0,
            manager_name_source=None,
            fund_name_source=scope.fund_name_snapshot,
            umbrella_name_source=None,
            monthly_report_url=(
                source_url
                if report_kind == "monthly" and source_url and source_url.startswith(("http://", "https://"))
                else None
            ),
            fallback_report_url=(
                source_url
                if report_kind != "monthly" and source_url and source_url.startswith(("http://", "https://"))
                else None
            ),
            run_command=None,
            match_status="matched",
            match_method="manual_target_and_model_match",
            match_confidence="high",
            is_active=True,
            metadata_json=metadata,
        )
        session.add(existing)
    else:
        existing.metadata_json = metadata
        existing.fund_id = scope.fund_id
        if source_url and source_url.startswith(("http://", "https://")):
            if report_kind == "monthly":
                existing.monthly_report_url = source_url
            else:
                existing.fallback_report_url = source_url
        existing.is_active = True


def publish_job_manifest(
    session: Session,
    *,
    job_id: uuid.UUID,
    manifest_path: str,
    manifest_sha256: str,
    lease_token: str,
) -> PublishResultData:
    """验证 Worker manifest 并原子发布；参数 session/job/manifest。"""

    settings = get_settings()
    project_root = settings.project_root
    job = session.get(Job, job_id)
    if job is None:
        raise ApiError(404, "E_JOB_NOT_FOUND", "任务不存在")
    if job.status not in {"validating", "running"}:
        raise ApiError(
            409,
            "E_JOB_STATE_CONFLICT",
            f"任务状态 {job.status} 不允许发布",
        )
    if job.batch_id is None or job.base_batch_id is None:
        raise ApiError(422, "E_PUBLISH_MANIFEST", "任务缺少批次引用")
    lock = session.scalar(
        select(JobLock).where(
            JobLock.lock_name == "global_write",
            JobLock.job_id == job.id,
        )
    )
    if lock is None:
        raise ApiError(409, "E_LOCK_LOST", "Worker 已失去全局写锁")
    if lock.lease_token != lease_token:
        raise ApiError(409, "E_LEASE_FENCED", "Worker 租约已被新的执行尝试替换")

    path = Path(manifest_path)
    if not path.is_absolute():
        path = resolve_managed_path(settings, manifest_path)
    path = _safe_manifest_file(path, settings.data_root)
    if not path.is_file():
        raise ApiError(422, "E_PUBLISH_MANIFEST", "manifest 文件不存在")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != manifest_sha256:
        raise ApiError(409, "E_FILE_HASH_MISMATCH", "manifest 哈希不一致")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("job_id") != str(job.id):
        raise ApiError(422, "E_PUBLISH_MANIFEST", "manifest 与任务不匹配")
    if manifest.get("lease_token") != lease_token:
        raise ApiError(409, "E_LEASE_FENCED", "manifest 租约令牌不匹配")
    if manifest.get("worker_id") != lock.owner:
        raise ApiError(409, "E_LEASE_FENCED", "manifest Worker 不是当前租约持有者")
    staging_root = path.parent
    verified_files = []
    for item in manifest.get("files") or []:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ApiError(422, "E_PUBLISH_MANIFEST", "manifest 文件路径非法")
        artifact = staging_root / relative
        if not artifact.is_file():
            raise ApiError(422, "E_PUBLISH_MANIFEST", f"缺少产物：{relative}")
        artifact_digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        if artifact_digest != item["sha256"]:
            raise ApiError(409, "E_FILE_HASH_MISMATCH", f"产物哈希不一致：{relative}")
        verified_files.append(relative)

    batch = session.get(DatasetBatch, job.batch_id)
    if batch is None or batch.status != "staging":
        raise ApiError(422, "E_PUBLISH_MANIFEST", "任务批次不是 staging")
    final_dir = settings.data_root / "batches" / str(batch.id)
    if final_dir.exists():
        raise ApiError(409, "E_PUBLISH_CONFLICT", "发布目录已存在")
    temp_dir = final_dir.parent / f".{batch.id}.{uuid.uuid4().hex}.tmp"
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True)
    for relative in verified_files:
        destination = temp_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staging_root / relative, destination)
    published_manifest = temp_dir / "manifest.json"
    published_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    published_sha = hashlib.sha256(published_manifest.read_bytes()).hexdigest()
    os.replace(temp_dir, final_dir)
    relative_manifest = logical_data_path(final_dir / "manifest.json", settings.data_root)

    job.status = "publishing"
    append_event(
        session,
        job,
        "job.publish",
        {"status": "publishing", "batch_id": str(batch.id)},
    )
    _clone_batch_results(
        session,
        source_batch_id=job.base_batch_id,
        target_batch_id=batch.id,
    )
    scopes = list(
        session.scalars(
            select(JobFundScope).where(JobFundScope.job_id == job.id)
        )
    )
    requested_stages = set(job.requested_stages or [])
    if "structure" in requested_stages:
        task_batch_root = final_dir / "staging" / str(job.id)
        try:
            importer = D1ImportService(
                session,
                project_root=project_root,
            )
            summary = importer.import_structured_into_batch(
                batch,
                task_batch_root,
                {scope.fund_id for scope in scopes},
            )
            if summary.structured_records == 0 or summary.skipped_records:
                raise ApiError(
                    422,
                    "E_PUBLISH_STRUCTURED",
                    (
                        "结构化结果未能完整匹配任务基金："
                        f"记录 {summary.structured_records}，"
                        f"跳过 {summary.skipped_records}"
                    ),
                )
            _publish_supplementary_source(
                session,
                job=job,
                batch=batch,
                scopes=scopes,
            )
        except Exception:
            shutil.rmtree(final_dir, ignore_errors=True)
            raise
    failed = sum(1 for item in scopes if item.result_status == "failed")
    skipped = sum(1 for item in scopes if item.result_status == "skipped")
    outcome = "completed_with_gaps" if failed or skipped else "complete"
    batch.manifest_path = relative_manifest.as_posix()
    batch.status = "published"
    batch.published_at = datetime.now(timezone.utc)
    batch.source_sha256 = published_sha
    state = session.get(PublicationState, 1)
    if state is None:
        state = PublicationState(id=1)
        session.add(state)
    state.current_batch_id = batch.id
    state.published_at = batch.published_at
    state.published_by = "api-publisher"
    state.release_version = "d3-task-v1"
    current_snapshot_id = batch.id
    if job.task_target == "master_database" and config_from_job(job).auto_import:
        master_path = final_dir / "staging" / str(job.id) / "master_database" / "HKEX基金综合资料库_latest.xlsx"
        if not master_path.is_file():
            shutil.rmtree(final_dir, ignore_errors=True)
            raise ApiError(422, "E_MASTER_ARTIFACT_NOT_FOUND", "综合资料库缓存文件不存在，无法自动落库")
        try:
            master_importer = D1ImportService(session, project_root=project_root)
            master_summary = master_importer.import_hkex_master_workbook(master_path, activate=True)
            current_snapshot_id = UUID(master_summary.batch_id)
            append_event(
                session,
                job,
                "job.publish",
                {
                    "status": "master_imported",
                    "batch_id": master_summary.batch_id,
                    "summary": master_summary.to_dict(),
                },
            )
        except Exception:
            shutil.rmtree(final_dir, ignore_errors=True)
            raise
    job.status = "completed"
    job.outcome = outcome
    job.finished_at = state.published_at
    job.current_stage = None
    job.result_manifest_path = relative_manifest.as_posix()
    session.execute(
        text("DELETE FROM job_locks WHERE job_id = :job_id"),
        {"job_id": job.id},
    )
    append_event(
        session,
        job,
        "job.completed",
        {
            "status": "completed",
            "outcome": outcome,
            "snapshot_id": str(current_snapshot_id),
        },
    )
    session.commit()
    return PublishResultData(
        job_id=job.id,
        batch_id=batch.id,
        status="completed",
        manifest_path=relative_manifest.as_posix(),
        manifest_sha256=published_sha,
        current_snapshot_id=current_snapshot_id,
    )

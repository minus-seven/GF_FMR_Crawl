"""Verify a completed D3 job and its published manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import create_session_factory
from app.models import (
    DatasetBatch,
    Job,
    JobEvent,
    JobFundScope,
    JobLock,
    PublicationState,
)


def parse_args() -> argparse.Namespace:
    """解析 D3 核验参数；参数为空。"""

    parser = argparse.ArgumentParser(description="核验最近一次完成的 D3 任务。")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--job-id")
    return parser.parse_args()


def main() -> int:
    """核验任务、锁、批次、manifest 和文件哈希；参数为空。"""

    args = parse_args()
    factory = create_session_factory(args.database_url)
    project_root = get_settings().project_root
    with factory() as session:
        if args.job_id:
            job = session.get(Job, args.job_id)
        else:
            job = session.scalar(
                select(Job)
                .where(Job.status == "completed")
                .order_by(Job.finished_at.desc())
                .limit(1)
            )
        if job is None:
            print("[FAILED] no completed job")
            return 2
        scopes = list(
            session.scalars(
                select(JobFundScope).where(JobFundScope.job_id == job.id)
            )
        )
        events = int(
            session.scalar(
                select(func.count())
                .select_from(JobEvent)
                .where(JobEvent.job_id == job.id)
            )
            or 0
        )
        lock = session.scalar(
            select(JobLock).where(JobLock.job_id == job.id)
        )
        batch = session.get(DatasetBatch, job.batch_id) if job.batch_id else None
        state = session.get(PublicationState, 1)

    failures = []
    if job.status != "completed":
        failures.append(f"job status is {job.status}")
    if not scopes or any(item.result_status != "success" for item in scopes):
        failures.append("job scope did not fully succeed")
    if events < 4:
        failures.append("job events are incomplete")
    if lock is not None:
        failures.append("job still holds a lock")
    if batch is None or batch.status != "published":
        failures.append("result batch is not published")
    if state is None or state.current_batch_id != job.batch_id:
        failures.append("publication pointer does not reference result batch")
    manifest_path = (
        Path(batch.manifest_path)
        if batch and batch.manifest_path
        else None
    )
    if manifest_path and not manifest_path.is_absolute():
        manifest_path = project_root / manifest_path
    if manifest_path is None or not manifest_path.is_file():
        failures.append("manifest file is missing")
        manifest = None
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest.get("files") or []:
            artifact = manifest_path.parent / item["path"]
            if not artifact.is_file():
                failures.append(f"artifact missing: {item['path']}")
                continue
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            if digest != item["sha256"]:
                failures.append(f"artifact hash mismatch: {item['path']}")

    result = {
        "job_id": str(job.id),
        "job_status": job.status,
        "batch_id": str(job.batch_id) if job.batch_id else None,
        "scope_count": len(scopes),
        "event_count": events,
        "manifest_path": str(manifest_path) if manifest_path else None,
        "manifest_files": len(manifest.get("files") or []) if manifest else 0,
        "current_snapshot_matches": bool(
            state and state.current_batch_id == job.batch_id
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if failures:
        print("[FAILED] " + "; ".join(failures))
        return 2
    print("[OK] D3 verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

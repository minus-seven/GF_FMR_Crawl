"""Recovery, cancellation and publication rollback regression tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid

import pytest
from sqlalchemy import delete, select


TEST_DATABASE_URL = os.environ.get("PLATFORM_TEST_DATABASE_URL", "")
if TEST_DATABASE_URL:
    os.environ["PLATFORM_DATABASE_URL"] = TEST_DATABASE_URL


pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="PLATFORM_TEST_DATABASE_URL is not configured",
)


def _job_values(*, status: str, batch_id: uuid.UUID | None = None) -> dict:
    """构造测试任务必填字段；参数 status/batch_id 为状态和批次。"""

    marker = uuid.uuid4().hex
    return {
        "id": uuid.uuid4(),
        "requested_by": None,
        "status": status,
        "outcome": None,
        "batch_id": batch_id,
        "base_batch_id": None,
        "requested_stages": ["download"],
        "scope_definition": {"mode": "funds", "fund_codes": ["ALL924"]},
        "scope_hash": marker,
        "idempotency_key": f"test-{marker}",
        "request_hash": hashlib.sha256(marker.encode()).hexdigest(),
        "worker_id": None,
        "cancellation_requested": False,
        "error_code": None,
        "error_message": None,
        "result_manifest_path": None,
        "current_stage": None,
        "started_at": None,
        "finished_at": None,
    }


def test_expired_global_lease_is_reclaimed_and_job_can_be_claimed() -> None:
    """过期全局租约应被删除，等待任务可被新 Worker 领取。"""

    from app.db.session import create_session_factory
    from app.models import Job, JobLock, PublicationState
    from app.worker.main import Worker

    factory = create_session_factory(TEST_DATABASE_URL)
    worker = Worker.__new__(Worker)
    worker.factory = factory
    worker.worker_id = "replacement-worker"
    worker.poll_interval = 1
    job_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    with factory() as session:
        current_snapshot = session.scalar(
            select(PublicationState.current_batch_id).where(
                PublicationState.id == 1
            )
        )
        job = Job(**_job_values(status="waiting_lock"))
        job.id = job_id
        job.base_batch_id = current_snapshot
        session.add(job)
        session.add(
            JobLock(
                id=uuid.uuid4(),
                job_id=job_id,
                lock_name="global_write",
                owner="dead-worker",
                heartbeat_at=now - timedelta(minutes=20),
                expires_at=now - timedelta(minutes=10),
            )
        )
        session.commit()

    claimed = worker.claim_next()

    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.status == "running"
    assert claimed.worker_id == "replacement-worker"
    with factory() as session:
        lock = session.scalar(
            select(JobLock).where(JobLock.job_id == job_id)
        )
        assert lock is not None
        assert lock.owner == "replacement-worker"
        assert lock.expires_at > datetime.now(timezone.utc)
        session.execute(delete(JobLock).where(JobLock.job_id == job_id))
        session.execute(delete(Job).where(Job.id == job_id))
        session.commit()


def test_orphaned_running_job_is_failed_when_lease_expired() -> None:
    """运行中的孤儿任务应在租约过期后被标记失败并释放锁。"""

    from app.db.session import create_session_factory
    from app.models import DatasetBatch, Job, JobLock
    from app.worker.main import Worker

    factory = create_session_factory(TEST_DATABASE_URL)
    worker = Worker.__new__(Worker)
    worker.factory = factory
    worker.worker_id = "recovery-worker"
    now = datetime.now(timezone.utc)
    job_id = uuid.uuid4()
    batch_id = uuid.uuid4()
    with factory() as session:
        session.add(
            DatasetBatch(
                id=batch_id,
                status="staging",
                scope_hash=f"recovery-test-{batch_id}",
                source_kind="test",
            )
        )
        values = _job_values(status="running", batch_id=batch_id)
        values["id"] = job_id
        values["current_stage"] = "structure"
        values["started_at"] = now - timedelta(hours=2)
        session.add(Job(**values))
        session.flush()
        session.add(
            JobLock(
                id=uuid.uuid4(),
                job_id=job_id,
                lock_name="global_write",
                owner="dead-worker",
                heartbeat_at=now - timedelta(minutes=20),
                expires_at=now - timedelta(minutes=10),
            )
        )
        session.commit()

    recovered = worker.recover_orphaned_jobs()

    assert recovered == 1
    with factory() as session:
        job = session.get(Job, job_id)
        batch = session.get(DatasetBatch, batch_id)
        lock = session.scalar(select(JobLock).where(JobLock.job_id == job_id))
        assert job is not None
        assert job.status == "failed"
        assert job.error_code == "E_WORKER_LOST"
        assert job.current_stage is None
        assert batch is not None and batch.status == "failed"
        assert lock is None
        session.execute(delete(Job).where(Job.id == job_id))
        session.execute(delete(DatasetBatch).where(DatasetBatch.id == batch_id))
        session.commit()


def test_worker_run_forever_retries_after_database_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """数据库瞬断不应结束 Worker 轮询循环。"""

    from sqlalchemy.exc import OperationalError

    from app.worker.main import Worker

    worker = Worker.__new__(Worker)
    worker.poll_interval = 0.0
    worker._last_recovery = time.monotonic()
    calls = {"count": 0}

    def run_once() -> bool:
        """第一次模拟数据库断线，第二次终止循环。"""

        calls["count"] += 1
        if calls["count"] == 1:
            raise OperationalError("SELECT 1", {}, Exception("database down"))
        raise KeyboardInterrupt

    monkeypatch.setattr(worker, "_recover_if_due", lambda: None)
    monkeypatch.setattr(worker, "run_once", run_once)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    with pytest.raises(KeyboardInterrupt):
        worker.run_forever()
    assert calls["count"] == 2


def test_worker_heartbeat_survives_database_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """心跳线程遇到数据库瞬断应重试，而不是静默退出。"""

    from sqlalchemy.exc import OperationalError

    from app.worker.main import Worker

    worker = Worker.__new__(Worker)
    attempts = {"count": 0}

    class FailingFactory:
        """模拟断线的会话工厂；参数为空。"""

        def __call__(self):
            """抛出数据库断线；参数为空。"""

            attempts["count"] += 1
            raise OperationalError("SELECT 1", {}, Exception("database down"))

    worker.factory = FailingFactory()
    stop = threading.Event()
    cancel = threading.Event()
    waits = {"count": 0}

    def fake_wait(_timeout: float) -> bool:
        """第二次等待时结束循环；参数 _timeout 被忽略。"""

        waits["count"] += 1
        if waits["count"] >= 2:
            stop.set()
        return stop.is_set()

    monkeypatch.setattr(stop, "wait", fake_wait)

    worker._monitor(uuid.uuid4(), cancel, stop)

    assert attempts["count"] == 1
    assert waits["count"] == 2


def test_cancel_terminates_process_group_and_grandchild(tmp_path: Path) -> None:
    """取消外部命令时应同时终止子进程和孙进程。"""

    from app.worker.adapters import _run_fixed_process

    pid_file = tmp_path / "child.pid"
    log_path = tmp_path / "cancel.log"
    code = (
        "import pathlib,subprocess,time;"
        "p=subprocess.Popen(['sleep','30']);"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid));"
        "time.sleep(30)"
    )
    cancel = threading.Event()
    result: dict[str, object] = {}

    def run() -> None:
        """执行可取消测试进程；参数为空。"""

        result["value"] = _run_fixed_process(
            [sys.executable, "-c", code],
            cwd=tmp_path,
            log_path=log_path,
            cancel_event=cancel,
            timeout=20,
        )

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 5
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert pid_file.exists()
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    cancel.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert result["value"][1] is True
    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_publish_hash_failure_keeps_current_snapshot_and_batch_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """发布文件哈希失败时不得切换发布指针，批次仍保持 staging。"""

    from app.api.errors import ApiError
    from app.db.session import create_session_factory
    from app.models import DatasetBatch, Job, JobLock, PublicationState
    from app.services.publication import publish_job_manifest

    factory = create_session_factory(TEST_DATABASE_URL)
    batch_id = uuid.uuid4()
    job_id = uuid.uuid4()
    job_root = tmp_path / "data" / "jobs" / str(job_id)
    job_root.mkdir(parents=True)
    manifest_path = job_root / "staging_manifest.json"
    lease_token = "a" * 32
    manifest_path.write_text(
        json.dumps({"job_id": str(job_id), "files": [], "lease_token": lease_token, "worker_id": "publish-test"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.services.publication.get_settings",
        lambda: type("Settings", (), {"project_root": tmp_path})(),
    )
    with factory() as session:
        before = session.scalar(
            select(PublicationState.current_batch_id).where(
                PublicationState.id == 1
            )
        )
        batch = DatasetBatch(
            id=batch_id,
            status="staging",
            scope_hash=f"publish-test-{batch_id}",
            source_kind="test",
        )
        session.add(batch)
        session.flush()
        values = _job_values(status="validating", batch_id=batch_id)
        values["id"] = job_id
        values["base_batch_id"] = before
        session.add(Job(**values))
        session.flush()
        session.add(
            JobLock(
                id=uuid.uuid4(),
                job_id=job_id,
                lock_name="global_write",
                owner="publish-test",
                lease_token=lease_token,
                heartbeat_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
        )
        session.commit()

    with factory() as session:
        with pytest.raises(ApiError, match="manifest 哈希不一致"):
            publish_job_manifest(
                session,
                job_id=job_id,
                manifest_path=str(manifest_path),
                manifest_sha256="0" * 64,
                lease_token=lease_token,
            )
        session.rollback()
        after = session.scalar(
            select(PublicationState.current_batch_id).where(
                PublicationState.id == 1
            )
        )
        batch = session.get(DatasetBatch, batch_id)
        assert after == before
        assert batch is not None and batch.status == "staging"
        session.execute(delete(JobLock).where(JobLock.job_id == job_id))
        session.execute(delete(Job).where(Job.id == job_id))
        session.execute(delete(DatasetBatch).where(DatasetBatch.id == batch_id))
        session.commit()

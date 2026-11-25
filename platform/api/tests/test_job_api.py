"""D3 task API integration tests."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest


TEST_DATABASE_URL = os.environ.get("PLATFORM_TEST_DATABASE_URL", "")
if TEST_DATABASE_URL:
    os.environ["PLATFORM_DATABASE_URL"] = TEST_DATABASE_URL

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db.session import create_session_factory  # noqa: E402
from app.importers.common import sha256_json  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    DatasetBatch,
    Fund,
    Job,
    JobEvent,
    JobFundScope,
    JobStep,
    PublicationState,
)


pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="PLATFORM_TEST_DATABASE_URL is not configured",
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    """创建任务 API 客户端；参数为空。"""

    return TestClient(app, raise_server_exceptions=False)


def _body(stages: list[str] | None = None) -> dict:
    """构造单基金任务请求；参数 stages：阶段列表。"""

    return {
        "scope": {"mode": "funds", "fund_codes": ["ALL924"]},
        "stages": stages or ["download"],
        "document_policy": {"prefer": "monthly"},
        "model_policy": {},
    }


def test_scope_options_and_batch_scope_modes(client: TestClient) -> None:
    """任务应支持全量、管理人和资产类别范围并冻结正确基金数。"""

    options_response = client.get("/api/jobs/scope-options")
    assert options_response.status_code == 200
    options = options_response.json()["data"]
    assert options["total_funds"] == 2479
    assert options["managers"]
    assert options["categories"]

    cases = [
        {"mode": "all"},
        {
            "mode": "manager",
            "manager_ids": [options["managers"][0]["manager_id"]],
        },
        {
            "mode": "category",
            "categories": [options["categories"][0]["category"]],
        },
    ]
    for scope in cases:
        response = client.post(
            "/api/jobs",
            headers={"Idempotency-Key": f"scope-{uuid.uuid4()}"},
            json={
                **_body(),
                "scope": scope,
            },
        )
        assert response.status_code == 202
        body = response.json()["data"]
        assert body["scope_count"] == (
            options["total_funds"]
            if scope["mode"] == "all"
            else options["managers"][0]["fund_count"]
            if scope["mode"] == "manager"
            else options["categories"][0]["fund_count"]
        )
        assert body["scope_display"]["mode"] == scope["mode"]
        assert body["scope_display"]["primary"]
        client.post(f"/api/jobs/{body['job_id']}/cancel")


def test_job_idempotency_scope_freeze_and_global_lock(client: TestClient) -> None:
    """任务应冻结范围、支持幂等并阻止第二个写任务。"""

    key1 = f"test-{uuid.uuid4()}"
    key2 = f"test-{uuid.uuid4()}"
    first = client.post("/api/jobs", headers={"Idempotency-Key": key1}, json=_body())
    repeated = client.post("/api/jobs", headers={"Idempotency-Key": key1}, json=_body())
    second = client.post("/api/jobs", headers={"Idempotency-Key": key2}, json=_body())
    conflict = client.post(
        "/api/jobs",
        headers={"Idempotency-Key": key1},
        json=_body(["parse"]),
    )

    assert first.status_code == 202
    assert repeated.status_code == 200
    assert repeated.json()["data"]["job_id"] == first.json()["data"]["job_id"]
    assert first.json()["data"]["scope_count"] == 1
    assert first.json()["data"]["status"] == "queued"
    assert first.json()["data"]["requested_stages"] == ["download"]
    assert first.json()["data"]["scope_display"]["primary"]
    assert first.json()["data"]["scope_display"]["secondary"] == "ALL924"
    assert second.status_code == 202
    assert second.json()["data"]["status"] == "waiting_lock"
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "E_IDEMPOTENCY_CONFLICT"

    first_id = first.json()["data"]["job_id"]
    second_id = second.json()["data"]["job_id"]
    assert client.post(f"/api/jobs/{first_id}/cancel").json()["data"]["status"] == "cancelled"
    assert client.post(f"/api/jobs/{second_id}/cancel").json()["data"]["status"] == "cancelled"


def test_cancelled_job_sse_terminates_and_replays_events(client: TestClient) -> None:
    """终态任务 SSE 应返回有序事件并结束。"""

    key = f"test-{uuid.uuid4()}"
    created = client.post("/api/jobs", headers={"Idempotency-Key": key}, json=_body())
    job_id = created.json()["data"]["job_id"]
    client.post(f"/api/jobs/{job_id}/cancel")

    lines = []
    with client.stream("GET", f"/api/jobs/{job_id}/events", timeout=5) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    event_lines = [line for line in lines if line.startswith("event: ")]
    data_lines = [line for line in lines if line.startswith("data: ")]
    assert event_lines
    assert data_lines
    assert event_lines[-1] == "event: job.completed"


def test_chained_stage_and_fund_failure_reasons(client: TestClient) -> None:
    """后续阶段应引用已完成前序任务，并返回基金跳过/失败原因。"""

    factory = create_session_factory(TEST_DATABASE_URL)
    scope = {
        "mode": "funds",
        "manager_ids": [],
        "categories": [],
        "fund_codes": ["ALL924"],
        "tag_filters": [],
    }
    with factory() as session:
        fund = session.scalar(select(Fund).where(Fund.fund_code == "ALL924"))
        state = session.get(PublicationState, 1)
        assert fund is not None and state is not None
        source_id = uuid.uuid4()
        source = Job(
            id=source_id,
            status="completed",
            outcome="complete",
            requested_stages=["download"],
            scope_definition=scope,
            scope_hash=sha256_json(scope),
            idempotency_key=f"source-{source_id}",
            request_hash="source",
            cancellation_requested=False,
            batch_id=state.current_batch_id,
            base_batch_id=state.current_batch_id,
            created_at=datetime.now(timezone.utc),
        )
        session.add(source)
        session.add(
            JobStep(
                id=uuid.uuid4(),
                job_id=source_id,
                step_type="download",
                status="completed",
                total_count=1,
                success_count=1,
                skip_count=0,
                failure_count=0,
            )
        )
        session.add(
            JobFundScope(
                id=uuid.uuid4(),
                job_id=source_id,
                fund_id=fund.id,
                fund_code_snapshot=fund.fund_code,
                fund_name_snapshot=fund.name_cn,
                result_status="skipped",
                skip_reason="测试跳过原因",
            )
        )
        session.add(
            JobEvent(
                id=uuid.uuid4(),
                job_id=source_id,
                sequence_no=1,
                event_type="job.fund",
                payload={
                    "stage": "parse",
                    "fund_code": fund.fund_code,
                    "fund_name": fund.name_cn,
                    "status": "failed",
                    "reason": "测试解析失败",
                },
                created_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    chained = client.post(
        "/api/jobs",
        headers={"Idempotency-Key": f"chain-{uuid.uuid4()}"},
        json={**_body(["parse"]), "input_job_id": str(source_id)},
    )
    assert chained.status_code == 202
    chained_id = chained.json()["data"]["job_id"]
    client.post(f"/api/jobs/{chained_id}/cancel")

    reasons = client.get(
        f"/api/jobs/{source_id}/funds",
        params=[("result_status", "failed"), ("result_status", "skipped")],
    )
    assert reasons.status_code == 200
    assert reasons.json()["data"]["total"] == 1
    assert reasons.json()["data"]["items"][0]["fund_code"] == "ALL924"
    assert reasons.json()["data"]["items"][0]["skip_reason"] == "测试跳过原因"

    issues = client.get(f"/api/jobs/{source_id}/issues")
    assert issues.status_code == 200
    issue_data = issues.json()["data"]
    assert issue_data["total"] == 2
    assert issue_data["skipped"] == 1
    assert issue_data["failed"] == 1
    assert issue_data["items"][0]["stage"] == "download"
    assert issue_data["items"][0]["reason"] == "测试跳过原因"
    assert issue_data["items"][1]["stage"] == "parse"
    assert issue_data["items"][1]["reason"] == "测试解析失败"


def test_failed_job_retry_resumes_only_incomplete_stages(
    client: TestClient,
) -> None:
    """失败任务续跑应保留已完成阶段并重置失败及后续阶段。"""

    factory = create_session_factory(TEST_DATABASE_URL)
    scope = {
        "mode": "funds",
        "manager_ids": [],
        "categories": [],
        "fund_codes": ["ALL924"],
        "tag_filters": [],
    }
    with factory() as session:
        fund = session.scalar(select(Fund).where(Fund.fund_code == "ALL924"))
        state = session.get(PublicationState, 1)
        assert fund is not None and state is not None
        job_id = uuid.uuid4()
        batch_id = uuid.uuid4()
        session.add(
            DatasetBatch(
                id=batch_id,
                status="failed",
                scope_hash=sha256_json(scope),
                source_kind="task",
            )
        )
        session.add(
            Job(
                id=job_id,
                status="failed",
                outcome=None,
                requested_stages=["download", "parse", "structure"],
                scope_definition={**scope, "input_job_id": None},
                scope_hash=sha256_json(scope),
                idempotency_key=f"retry-{job_id}",
                request_hash="retry",
                cancellation_requested=False,
                batch_id=batch_id,
                base_batch_id=state.current_batch_id,
                error_code="E_TASK_EXECUTION",
                error_message="parse failed",
                created_at=datetime.now(timezone.utc),
            )
        )
        session.add_all(
            [
                JobStep(
                    id=uuid.uuid4(),
                    job_id=job_id,
                    step_type="download",
                    status="completed",
                    total_count=1,
                    success_count=1,
                    skip_count=0,
                    failure_count=0,
                ),
                JobStep(
                    id=uuid.uuid4(),
                    job_id=job_id,
                    step_type="parse",
                    status="failed",
                    total_count=1,
                    success_count=0,
                    skip_count=0,
                    failure_count=1,
                ),
                JobStep(
                    id=uuid.uuid4(),
                    job_id=job_id,
                    step_type="structure",
                    status="pending",
                    total_count=1,
                    success_count=0,
                    skip_count=0,
                    failure_count=0,
                ),
            ]
        )
        session.commit()

    retried = client.post(f"/api/jobs/{job_id}/retry")
    assert retried.status_code == 200
    data = retried.json()["data"]
    statuses = {
        step["step_type"]: step["status"]
        for step in data["steps"]
    }
    assert data["status"] == "queued"
    assert statuses == {
        "download": "completed",
        "parse": "pending",
        "structure": "pending",
    }
    assert data["error_code"] is None
    client.post(f"/api/jobs/{job_id}/cancel")

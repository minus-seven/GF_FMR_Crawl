"""Database-backed D2 API integration tests."""

from __future__ import annotations

import os
from statistics import quantiles
import time

import pytest


TEST_DATABASE_URL = os.environ.get("PLATFORM_TEST_DATABASE_URL", "")
if TEST_DATABASE_URL:
    os.environ["PLATFORM_DATABASE_URL"] = TEST_DATABASE_URL

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="PLATFORM_TEST_DATABASE_URL is not configured",
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    """创建 API 测试客户端；参数为空。"""

    return TestClient(app, raise_server_exceptions=False)


def test_health_and_snapshot(client: TestClient) -> None:
    """健康检查和当前快照应返回 2,479 只基金与 853 条补充来源。"""

    health = client.get("/api/health")
    snapshot = client.get("/api/snapshots/current")

    assert health.status_code == 200
    assert health.json()["data"]["database"] == "ok"
    assert health.json()["data"]["fund_count"] == 2479
    assert health.json()["data"]["report_source_count"] == 853
    assert snapshot.status_code == 200
    assert snapshot.json()["data"]["status"] == "published"
    assert snapshot.json()["data"]["fund_count"] == 2479
    assert snapshot.json()["data"]["published_at"].endswith("Z")


def test_fund_list_filters_and_pagination(client: TestClient) -> None:
    """基金列表应支持分页、关键词、管理人和资产类别过滤。"""

    first_page = client.get("/api/funds", params={"page": 1, "page_size": 25})
    manager_page = client.get(
        "/api/funds",
        params={"manager": "摩根大通", "page": 1, "page_size": 10},
    )
    category_page = client.get(
        "/api/funds",
        params={"category": "固收", "page": 1, "page_size": 10},
    )
    search_page = client.get(
        "/api/funds",
        params={"q": "柏瑞环球债券基金", "page": 1, "page_size": 10},
    )

    assert first_page.status_code == 200
    assert first_page.json()["data"]["total"] == 2479
    assert len(first_page.json()["data"]["items"]) == 25
    assert manager_page.json()["data"]["total"] == 106
    assert category_page.json()["data"]["total"] == 669
    assert search_page.json()["data"]["total"] == 1
    assert search_page.json()["data"]["items"][0]["fund_code"] == "ALL924"


def test_fund_detail_metrics_quality_and_redirection(client: TestClient) -> None:
    """基金详情和指标应来自当前批次，并保留来源和质量状态。"""

    detail = client.get("/api/funds/ALL924")
    metrics = client.get("/api/funds/ALL924/metrics")
    missing = client.get("/api/funds/NOT-A-FUND")

    assert detail.status_code == 200
    payload = detail.json()["data"]
    assert payload["fund_code"] == "ALL924"
    assert payload["metrics"]
    assert payload["holdings"]
    assert payload["allocations"]
    assert payload["report_sources"]
    assert payload["latest_document"]
    assert metrics.status_code == 200
    assert metrics.json()["data"]["items"]
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "E_FUND_NOT_FOUND"
    assert missing.json()["request_id"] == missing.headers["x-request-id"]


def test_quality_and_echarts_endpoints(client: TestClient) -> None:
    """质量问题和两个 ECharts 视图应返回当前快照数据。"""

    quality = client.get("/api/quality/issues", params={"page_size": 20})
    charts = client.get(
        "/api/analytics/perspectives",
        params={
            "scope_fund_codes": ["AFG988", "BSG677", "BPY216"],
            "period": "latest",
        },
    )

    assert quality.status_code == 200
    assert quality.json()["data"]["total"] == 9
    assert charts.status_code == 200
    views = charts.json()["data"]["views"]
    assert [view["view_id"] for view in views] == [
        "fund_scale_histogram",
        "fund_scale_trend",
    ]
    assert views[0]["dataset"]
    assert views[1]["dataset"]
    assert charts.json()["data"]["scope_count"] == 3
    assert views[0]["quality"] == "complete"

    # 大范围基金集合必须走 JSON body，不能把数千个代码拼进 GET URL。
    large_scope = client.post(
        "/api/analytics/perspectives/query",
        json={
            "scope_fund_codes": ["AFG988"] * 101,
            "period": "latest",
        },
    )
    assert large_scope.status_code == 200
    assert large_scope.json()["data"]["scope_count"] == 1


def test_request_id_and_query_performance_smoke(client: TestClient) -> None:
    """基金详情查询保留请求 ID，并达到 2 秒 P95 冒烟目标。"""

    response = client.get(
        "/api/funds/ALL924",
        headers={"X-Request-Id": "req_d2_performance"},
    )
    assert response.headers["x-request-id"] == "req_d2_performance"
    assert response.json()["request_id"] == "req_d2_performance"

    durations = []
    for _ in range(20):
        started = time.perf_counter()
        response = client.get("/api/funds/ALL924")
        durations.append(time.perf_counter() - started)
        assert response.status_code == 200
    p95 = quantiles(durations, n=20)[18]
    assert p95 < 2.0

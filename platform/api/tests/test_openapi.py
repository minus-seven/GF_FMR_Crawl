"""Static checks for the generated D2 OpenAPI document."""

from app.main import app


def test_d2_routes_are_present_in_openapi() -> None:
    """OpenAPI 必须包含 D2 的全部查询路由。"""

    paths = app.openapi()["paths"]

    assert {
        "/api/health",
        "/api/snapshots/current",
        "/api/funds",
        "/api/funds/{fund_code}",
        "/api/funds/{fund_code}/metrics",
        "/api/quality/issues",
        "/api/analytics/perspectives",
        "/api/analytics/perspectives/query",
        "/api/jobs",
        "/api/jobs/scope-options",
        "/api/jobs/{job_id}",
        "/api/jobs/{job_id}/events",
        "/api/jobs/{job_id}/cancel",
        "/api/jobs/{job_id}/retry",
        "/api/jobs/{job_id}/issues",
        "/api/knowledge/search",
        "/api/knowledge/status",
        "/api/chat",
        "/api/agent/plan",
        "/api/agent/runs/{run_id}",
        "/api/agent/runs/{run_id}/confirm",
    }.issubset(paths)

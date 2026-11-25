"""Unit tests for task stage scheduling order."""

import pytest
from pydantic import ValidationError

from app.schemas.jobs import JobCreateRequest


def _request(stages: list[str]) -> dict:
    """构造阶段顺序测试请求；参数 stages 为原始阶段列表。"""

    return {
        "scope": {"mode": "funds", "fund_codes": ["ALL924"]},
        "stages": stages,
    }


def test_job_request_normalizes_stage_order() -> None:
    """阶段必须按下载、解析、结构化顺序执行。"""

    request = JobCreateRequest(**_request(["structure", "download", "parse"]))
    assert [stage.value for stage in request.stages] == [
        "download",
        "parse",
        "structure",
    ]


def test_job_request_rejects_download_without_parse_before_structure() -> None:
    """下载后直接结构化应在前端/接口层被拒绝。"""

    with pytest.raises(ValidationError, match="必须包含解析阶段"):
        JobCreateRequest(**_request(["structure", "download"]))

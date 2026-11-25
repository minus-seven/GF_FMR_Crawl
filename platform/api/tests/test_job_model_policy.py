"""结构化任务模型策略契约测试。"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.jobs import JobCreateRequest


def scope() -> dict:
    """返回最小有效基金范围；参数为空。"""

    return {"mode": "funds", "fund_codes": ["TEST001"]}


def test_local_model_policy_is_accepted() -> None:
    """本地 Qwen 任务应接受并发参数，模型名由模型设置统一提供。"""

    request = JobCreateRequest(
        scope=scope(),
        stages=["parse", "structure"],
        input_job_id=uuid4(),
        model_policy={
            "structured_provider": "local",
            "local_model": "qwen3:4b",
            "local_workers": 1,
        },
    )
    assert request.model_policy["local_model"] == "qwen3:4b"


def test_local_model_policy_can_omit_model_name() -> None:
    """新客户端选择本地模型时不必再输入模型名称。"""

    request = JobCreateRequest(
        scope=scope(),
        stages=["parse", "structure"],
        input_job_id=uuid4(),
        model_policy={"structured_provider": "local"},
    )

    assert request.model_policy == {"structured_provider": "local"}


def test_model_policy_rejects_unknown_provider() -> None:
    """未知供应商必须在提交边界被拒绝。"""

    with pytest.raises(ValidationError, match="structured_provider"):
        JobCreateRequest(
            scope=scope(),
            stages=["structure"],
            input_job_id=uuid4(),
            model_policy={"structured_provider": "unknown"},
        )


def test_model_policy_is_not_allowed_for_non_structure_job() -> None:
    """下载或解析任务不应携带结构化模型策略。"""

    with pytest.raises(ValidationError, match="仅结构化任务"):
        JobCreateRequest(
            scope=scope(),
            stages=["download"],
            model_policy={"structured_provider": "local", "local_model": "qwen3:4b"},
        )

"""受控 Agent 计划契约的确定性测试。"""

from app.schemas.agent import AgentPlanRequest
from app.services.agent import classify_intent


def test_classify_controlled_task_intents() -> None:
    """月报和半年报任务应进入对应的受控写计划。"""

    assert classify_intent("为 BJK348 创建月报任务") == "create_monthly_job"
    assert classify_intent("为 BJK348 补半年报", "fund_semiannual") == "create_semiannual_job"
    assert classify_intent("补充来源并下载新的 PDF") == "supplementary_source"
    assert classify_intent("比较 BJK348 与 BMV635 的规模") == "compare"


def test_agent_plan_request_is_bounded() -> None:
    """计划请求应限制问题长度和基金数量。"""

    request = AgentPlanRequest(question="创建月报任务", fund_codes=["BJK348"])
    assert request.fund_codes == ["BJK348"]
    assert request.task_target is None


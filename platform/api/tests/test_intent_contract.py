"""统一意图识别和受控路由契约测试。"""

from datetime import date

from app.services.intent import (
    candidate_intents,
    classify_intent_envelope,
    preparse_question,
    route_intent,
)


def test_preparse_extracts_fund_period_report_and_metric() -> None:
    """规则预解析应提取基金、报告期、报告类型和指标槽位。"""

    parsed = preparse_question("比较 BPJ877 与 BQN919 2026年9月月报的到期收益率")
    assert parsed["fund_codes"] == ["BPJ877", "BQN919"]
    assert parsed["report_date"] == date(2026, 9, 1)
    assert parsed["report_type"] == "monthly"
    assert parsed["metric"] == "portfolio_ytm_percent"


def test_intent_envelope_routes_read_query() -> None:
    """明确指标问题应进入只读指标工具。"""

    envelope = classify_intent_envelope("BPJ877 2026年9月到期收益率")
    decision = route_intent(envelope)
    assert envelope.intent == "query_metric"
    assert envelope.needs_clarification is False
    assert decision.tool_name == "query_metric"
    assert decision.requires_confirmation is False


def test_ambiguous_query_requires_clarification() -> None:
    """缺少基金范围的比较问题不得静默扩大为全量基金。"""

    envelope = classify_intent_envelope("比较 2026年9月的到期收益率")
    decision = route_intent(envelope)
    assert envelope.intent == "compare_funds"
    assert envelope.needs_clarification is True
    assert decision.route == "clarification"
    assert decision.allowed is False


def test_task_intent_stays_confirmation_bound() -> None:
    """写任务意图应进入计划工具并保留确认门。"""

    envelope = classify_intent_envelope("为 BPJ877 创建半年报任务")
    decision = route_intent(envelope)
    assert envelope.intent == "plan_semiannual_job"
    assert envelope.operation == "plan"
    assert decision.tool_name == "create_plan"
    assert decision.requires_confirmation is True


def test_candidates_keep_reasons_for_audit() -> None:
    """候选意图应保留命中原因，供审计和评测使用。"""

    candidates = candidate_intents("为什么 BTE502 月报没有持仓")
    assert candidates[0].intent == "trace_quality_issue"
    assert candidates[0].reasons

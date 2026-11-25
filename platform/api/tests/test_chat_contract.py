"""SQL 优先问答的确定性意图和契约测试。"""

from datetime import date

from app.schemas.chat import ChatRequest
from app.services.chat import (
    detect_question_intent,
    extract_fund_codes,
    extract_report_period,
)


def test_extract_scope_and_period() -> None:
    """问题中的基金代码和报告月份应可确定性解析。"""

    question = "比较 BJK348 与 BMV635 2026年9月30日的基金规模"
    assert extract_fund_codes(question) == ["BJK348", "BMV635"]
    assert extract_report_period(question) == date(2026, 9, 1)


def test_detect_sql_metric_and_holding_intents() -> None:
    """常见指标、持仓问题应路由到 SQL。"""

    assert detect_question_intent("BJK348 的年化波动率").metric_name == (
        "annualized_volatility_percent"
    )
    assert detect_question_intent("BJK348 前五大持仓").top_n == 5
    assert detect_question_intent("BJK348 的投资策略").metric_name == (
        "investment_strategy"
    )


def test_chat_request_contract_limits_vector_length() -> None:
    """问答请求应限制查询向量长度，避免绕过 1024 维契约。"""

    request = ChatRequest(question="BJK348 的规模", fund_codes=["BJK348"])
    assert request.mode == "hybrid"
    assert request.limit == 8

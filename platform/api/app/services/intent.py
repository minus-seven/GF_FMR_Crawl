"""统一的确定性意图预解析、置信度门和受控路由。"""

from __future__ import annotations

from datetime import date
import os
import re
from typing import Any

from app.schemas.intent import (
    IntentCandidate,
    IntentConstraints,
    IntentEnvelope,
    IntentScope,
    IntentTime,
    RouteDecision,
)


_FUND_CODE_RE = re.compile(r"(?<![A-Z0-9])[A-Z]{3}\d{3}(?![A-Z0-9])")
_DATE_PATTERNS = (
    re.compile(r"(?<!\d)(20\d{2})[-/.年](\d{1,2})(?:[-/.月](\d{1,2})日?)?"),
    re.compile(r"(?<!\d)(20\d{2})\s*年\s*(\d{1,2})\s*月"),
)
_CATEGORY_ALIASES = {
    "权益": "权益",
    "股票": "权益",
    "固收": "固收",
    "债券": "固收",
    "货币": "货币",
    "货币市场": "货币",
    "混合": "混合",
}
_METRIC_ALIASES = {
    "年化收益率": "annualized_return_percent",
    "年化回报": "annualized_return_percent",
    "年化波动率": "annualized_volatility_percent",
    "波动率": "annualized_volatility_percent",
    "日流动资产": "daily_liquid_assets_percent",
    "周流动资产": "weekly_liquid_assets_percent",
    "加权平均到期日": "weighted_average_maturity_days",
    "平均到期日": "weighted_average_maturity_days",
    "WAM": "weighted_average_maturity_days",
    "加权平均存续期": "weighted_average_life_days",
    "平均存续期": "weighted_average_life_days",
    "WAL": "weighted_average_life_days",
    "到期收益率": "portfolio_ytm_percent",
    "到期孳息率": "portfolio_ytm_percent",
    "YTM": "portfolio_ytm_percent",
    "组合久期": "portfolio_duration_years",
    "投资组合久期": "portfolio_duration_years",
    "基金规模": "fund_size_hkd_100m",
    "资产规模": "fund_size_hkd_100m",
    "成立日期": "inception_date",
    "成立时间": "inception_date",
    "投资目标": "investment_objective",
    "投資目標": "investment_objective",
    "投资策略": "investment_strategy",
    "投資策略": "investment_strategy",
}


def _extract_codes(question: str) -> list[str]:
    """提取基金代码；参数 question 为用户问题。"""

    return list(dict.fromkeys(_FUND_CODE_RE.findall(question.upper())))


def _extract_date(question: str) -> date | None:
    """提取报告日期；参数 question 为用户问题。"""

    for pattern in _DATE_PATTERNS:
        match = pattern.search(question)
        if not match:
            continue
        year, month, day = int(match.group(1)), int(match.group(2)), match.group(3)
        try:
            return date(year, month, int(day or 1))
        except ValueError:
            return None
    return None


def _report_type(question: str) -> str:
    """提取报告类型；参数 question 为用户问题。"""

    if any(token in question for token in ("半年报", "中报", "半年度")):
        return "semiannual"
    if any(token in question for token in ("年报", "年度报告", "年度")):
        return "annual"
    if any(token in question for token in ("月报", "月度报告", "月份")):
        return "monthly"
    return "unknown"


def _top_n(question: str) -> int | None:
    """提取持仓数量；参数 question 为用户问题。"""

    match = re.search(r"前\s*(\d+)|top\s*(\d+)", question, re.IGNORECASE)
    if match:
        return int(match.group(1) or match.group(2))
    if any(token in question for token in ("前十大", "前十", "主要持仓")):
        return 10
    if any(token in question for token in ("前五大", "前五")):
        return 5
    return None


def preparse_question(question: str) -> dict[str, Any]:
    """提取可确定槽位；参数 question 为用户原始问题。"""

    text = str(question or "").strip()
    metric = next(
        (value for alias, value in _METRIC_ALIASES.items() if alias.casefold() in text.casefold()),
        None,
    )
    category = next((value for alias, value in _CATEGORY_ALIASES.items() if alias in text), None)
    return {
        "fund_codes": _extract_codes(text),
        "report_date": _extract_date(text),
        "report_type": _report_type(text),
        "top_n": _top_n(text),
        "metric": metric,
        "category": category,
        "has_url": bool(re.search(r"https?://\S+", text, re.IGNORECASE)),
    }


def candidate_intents(question: str, task_target: str | None = None) -> list[IntentCandidate]:
    """生成候选意图；参数 question/task_target 为问题和显式任务目标。"""

    text = str(question or "").casefold()
    parsed = preparse_question(question)
    scores: dict[str, tuple[float, list[str]]] = {}

    def add(intent: str, score: float, reason: str) -> None:
        current = scores.get(intent, (0.0, []))
        scores[intent] = (max(current[0], score), [*current[1], reason])

    if parsed["metric"]:
        add("query_metric", 0.86, "指标别名命中")
    if parsed["top_n"] or any(token in text for token in ("持仓", "投资组合")):
        add("query_holdings", 0.84, "持仓或 Top N 命中")
    if any(token in text for token in ("比较", "对比", "差异", "排名", "最高", "最低")):
        add("compare_funds", 0.95 if parsed["fund_codes"] else 0.88, "比较动作命中")
    quality_signal = any(token in text for token in ("质量", "异常", "缺失", "为什么没有", "进入待复核", "有误", "待复核"))
    if any(token in text for token in ("投资目标", "投资策略", "说明", "解释")) and not quality_signal:
        add("explain_report", 0.90, "解释或报告文本命中")
    if any(token in text for token in ("质量", "异常", "缺失", "为什么没有", "为什么", "有误", "待复核")):
        add("trace_quality_issue", 0.94 if quality_signal else 0.88, "质量诊断动作命中")
    if any(token in text for token in ("原文", "哪一页", "页码", "来源在哪")):
        add("locate_source", 0.88, "来源定位动作命中")
    if any(token in text for token in ("缺少", "没有半年报", "缺失来源", "缺失月报来源", "哪些基金没有")):
        add("find_missing_data", 0.96 if not parsed["fund_codes"] else 0.86, "缺失数据动作命中")
    if task_target == "fund_monthly" or any(token in text for token in ("创建月报", "月报任务", "月报下载", "月报补数")):
        add("plan_monthly_job", 0.96, "月报任务动作命中")
    if task_target == "fund_semiannual" or any(token in text for token in ("创建半年报", "半年报任务", "半年报下载", "半年报补数", "补半年报")) or ("补" in text and "半年报" in text):
        add("plan_semiannual_job", 0.96, "半年报任务动作命中")
    if parsed["has_url"] or any(token in text for token in ("补充来源", "候选来源", "新地址")):
        add("plan_supplement_source", 0.95, "补充来源动作命中")
    if not scores:
        add("unknown", 0.25, "没有命中已注册意图")
    return [
        IntentCandidate(intent=intent, score=min(score, 0.99), reasons=reasons)
        for intent, (score, reasons) in sorted(scores.items(), key=lambda item: item[1][0], reverse=True)
    ]


def _envelope_from_candidate(question: str, candidate: IntentCandidate, task_target: str | None) -> IntentEnvelope:
    """将候选意图转换为统一契约；参数为问题、候选项和显式任务目标。"""

    parsed = preparse_question(question)
    intent = candidate.intent
    operation = "plan" if intent.startswith("plan_") else "read"
    domain = "task" if intent.startswith("plan_") else (
        "fund_metric" if intent == "query_metric" else
        "holding" if intent == "query_holdings" else
        "quality" if intent in {"trace_quality_issue", "find_missing_data"} else
        "report" if intent in {"explain_report", "locate_source"} else "fund_metric"
    )
    report_type = parsed["report_type"]
    if intent == "plan_monthly_job":
        report_type = "monthly"
    elif intent == "plan_semiannual_job":
        report_type = "semiannual"
    needs_clarification = candidate.score < 0.70
    if intent in {"compare_funds", "query_metric", "query_holdings"} and not parsed["fund_codes"] and not parsed["category"]:
        needs_clarification = True
    clarification = None
    if needs_clarification:
        clarification = "请补充基金代码或基金集合，并说明报告类型/报告期。"
    return IntentEnvelope(
        intent=intent,
        operation=operation,
        domain=domain,
        scope=IntentScope(fund_codes=parsed["fund_codes"], category=parsed["category"]),
        time=IntentTime(report_type=report_type, report_date=parsed["report_date"], latest="最新" in question),
        constraints=IntentConstraints(metric=parsed["metric"], top_n=parsed["top_n"]),
        output="task_plan" if operation == "plan" else "clarification" if needs_clarification else "answer_with_evidence",
        confidence=candidate.score,
        needs_clarification=needs_clarification,
        clarification_question=clarification,
        reasons=candidate.reasons,
    )


def classify_intent_envelope(
    question: str,
    task_target: str | None = None,
    *,
    allow_local_model: bool | None = None,
) -> IntentEnvelope:
    """识别统一意图；参数 question/task_target/allow_local_model 为问题、任务目标和是否启用本地模型。"""

    candidates = candidate_intents(question, task_target)
    envelope = _envelope_from_candidate(question, candidates[0], task_target)
    # 本阶段默认规则优先；本地模型通道由显式环境变量开启，避免问答主链路
    # 因 Ollama 不可用而变慢或改变既有结果。
    use_model = allow_local_model
    if use_model is None:
        use_model = os.environ.get("PLATFORM_AGENT_USE_LOCAL_MODEL", "0").casefold() in {"1", "true", "yes", "on"}
    if use_model and envelope.needs_clarification:
        from app.services.local_intent import classify_with_local_qwen

        model_result = classify_with_local_qwen(question, candidates)
        if model_result is not None:
            candidate_names = {item.intent for item in candidates}
            if model_result.intent in candidate_names or model_result.intent == "unknown":
                return _apply_safety_gate(model_result, question)
    return _apply_safety_gate(envelope, question)


def _apply_safety_gate(envelope: IntentEnvelope, question: str) -> IntentEnvelope:
    """复核模型意图的范围和副作用安全性；参数 envelope/question 为模型结果和原问题。"""

    scoped_intents = {
        "query_metric",
        "query_holdings",
        "compare_funds",
        "trace_quality_issue",
        "locate_source",
        "plan_monthly_job",
        "plan_semiannual_job",
        "plan_supplement_source",
    }
    has_scope = bool(
        envelope.scope.fund_codes
        or envelope.scope.collection_id
        or envelope.scope.category
        or envelope.scope.manager
    )
    if envelope.intent in scoped_intents and not has_scope:
        envelope.needs_clarification = True
        envelope.output = "clarification"
        envelope.clarification_question = "请补充基金代码或基金集合，并说明报告类型/报告期。"
    if envelope.intent.startswith("plan_"):
        envelope.operation = "plan"
        envelope.output = "task_plan" if not envelope.needs_clarification else "clarification"
    elif envelope.operation == "write":
        envelope.operation = "read"
        envelope.needs_clarification = True
        envelope.output = "clarification"
        envelope.clarification_question = "该操作需要先说明目标并经过用户确认。"
    return envelope


def route_intent(envelope: IntentEnvelope) -> RouteDecision:
    """将意图映射到受控工具；参数 envelope 为统一意图。"""

    routes = {
        "query_metric": ("query_metric", "read"),
        "query_holdings": ("query_holdings", "read"),
        "compare_funds": ("compare_funds", "read"),
        "explain_report": ("search_evidence", "read"),
        "trace_quality_issue": ("trace_quality", "read"),
        "locate_source": ("search_evidence", "read"),
        "find_missing_data": ("find_missing_data", "read"),
        "plan_monthly_job": ("create_plan", "plan"),
        "plan_semiannual_job": ("create_plan", "plan"),
        "plan_supplement_source": ("create_plan", "plan"),
    }
    if envelope.needs_clarification or envelope.intent == "unknown":
        return RouteDecision(route="clarification", allowed=False, reason=envelope.clarification_question or "无法安全识别意图")
    tool_name, route = routes.get(envelope.intent, (None, "read"))
    return RouteDecision(
        route=route,
        tool_name=tool_name,
        requires_confirmation=envelope.intent.startswith("plan_") or envelope.operation in {"plan", "write"},
        allowed=tool_name is not None,
        reason="意图已通过策略门",
    )

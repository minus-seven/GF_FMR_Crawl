"""知识问答 Agent 的意图、槽位和路由契约。"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


IntentName = Literal[
    "query_metric",
    "query_holdings",
    "compare_funds",
    "explain_report",
    "trace_quality_issue",
    "locate_source",
    "find_missing_data",
    "plan_monthly_job",
    "plan_semiannual_job",
    "plan_supplement_source",
    "unknown",
]
OperationName = Literal["read", "plan", "write"]
IntentDomain = Literal["fund_metric", "holding", "report", "quality", "task", "unknown"]
IntentOutput = Literal[
    "answer_with_evidence",
    "table",
    "evidence_only",
    "task_plan",
    "clarification",
]


class IntentScope(BaseModel):
    """意图作用范围；参数由规则或模型分类器填充。"""

    fund_codes: list[str] = Field(default_factory=list, max_length=1000)
    collection_id: str | None = None
    category: str | None = None
    manager: str | None = None


class IntentTime(BaseModel):
    """意图时间约束；参数 report_type/report_date/latest 为报告口径。"""

    report_type: Literal["monthly", "semiannual", "annual", "unknown"] = "unknown"
    report_date: date | None = None
    latest: bool = False


class IntentConstraints(BaseModel):
    """意图查询约束；参数为指标、持仓数量、币种和来源要求。"""

    metric: str | None = None
    top_n: int | None = Field(default=None, ge=1, le=100)
    currency: str | None = None
    source_requirement: str | None = None


class IntentEnvelope(BaseModel):
    """统一意图结果；参数由预解析器或本地模型分类器生成。"""

    schema_version: Literal["intent-v1"] = "intent-v1"
    intent: IntentName = "unknown"
    operation: OperationName = "read"
    domain: IntentDomain = "unknown"
    scope: IntentScope = Field(default_factory=IntentScope)
    time: IntentTime = Field(default_factory=IntentTime)
    constraints: IntentConstraints = Field(default_factory=IntentConstraints)
    output: IntentOutput = "clarification"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_clarification: bool = False
    clarification_question: str | None = None
    reasons: list[str] = Field(default_factory=list, max_length=20)
    classifier: Literal["rules", "local_qwen"] = "rules"
    classifier_model: str | None = None
    classifier_latency_ms: int | None = Field(default=None, ge=0)


class IntentCandidate(BaseModel):
    """规则层产生的候选意图；参数 intent/score/reasons 为候选信息。"""

    intent: IntentName
    score: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)


class RouteDecision(BaseModel):
    """意图到工具的策略决定；参数由策略门生成。"""

    route: str
    tool_name: str | None = None
    requires_confirmation: bool = False
    allowed: bool = True
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IntentRecognitionRequest(BaseModel):
    """意图识别请求；参数 question/task_target/allow_local_model 为问题、任务目标和模型开关。"""

    question: str = Field(min_length=1, max_length=2000)
    task_target: Literal["fund_monthly", "fund_semiannual"] | None = None
    allow_local_model: bool = False


class IntentRecognitionData(BaseModel):
    """意图识别结果；参数由规则候选、统一意图和路由策略填充。"""

    envelope: IntentEnvelope
    candidates: list[IntentCandidate] = Field(default_factory=list)
    route: RouteDecision

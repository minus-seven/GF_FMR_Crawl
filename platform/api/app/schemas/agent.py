"""受控 Agent 计划与确认契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.intent import IntentEnvelope


AgentIntent = Literal[
    "query",
    "compare",
    "supplementary_source",
    "create_monthly_job",
    "create_semiannual_job",
]
AgentRunStatus = Literal[
    "read_only",
    "awaiting_confirmation",
    "confirmed",
    "rejected",
    "failed",
    "needs_clarification",
]


class AgentPlanRequest(BaseModel):
    """生成受控计划；参数为自然语言问题、基金范围和可选任务策略。"""

    question: str = Field(min_length=1, max_length=2000)
    fund_codes: list[str] = Field(default_factory=list, max_length=1000)
    collection_id: UUID | None = None
    collection_session_key: str | None = Field(default=None, max_length=200)
    task_target: Literal["fund_monthly", "fund_semiannual"] | None = None
    structured_provider: Literal["deepseek", "qwen", "local"] | None = None


class AgentConfirmRequest(BaseModel):
    """确认或拒绝计划；参数 confirmed 表示是否允许创建任务。"""

    confirmed: bool


class AgentPlanData(BaseModel):
    """受控 Agent 计划和执行结果；参数由计划服务填充。"""

    run_id: UUID
    snapshot_id: UUID
    status: AgentRunStatus
    intent: AgentIntent
    intent_envelope: IntentEnvelope | None = None
    question: str
    plan: dict[str, Any] = Field(default_factory=dict)
    scope_definition: dict[str, Any] = Field(default_factory=dict)
    scope_count: int
    task_target: str | None = None
    model_policy: dict[str, Any] = Field(default_factory=dict)
    estimated_minutes: dict[str, int] = Field(default_factory=dict)
    impact_summary: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    requires_confirmation: bool
    confirmation_expires_at: datetime | None = None
    job_id: UUID | None = None


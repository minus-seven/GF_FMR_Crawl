"""受控 Agent 的意图识别、计划预览、确认和任务创建。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.models import (
    AgentMessage,
    AgentRun,
    Fund,
    FundClassification,
    FundCollection,
    FundCollectionItem,
    Job,
    Manager,
    ModelCall,
    ToolCall,
)
from app.schemas.agent import (
    AgentConfirmRequest,
    AgentIntent,
    AgentPlanData,
    AgentPlanRequest,
)
from app.schemas.jobs import (
    JobCreateRequest,
    JobScopeRequest,
    ScopeMode,
    StageName,
    TaskTarget,
)
from app.services.jobs import create_job
from app.services.intent import classify_intent_envelope
from app.services.model_settings import read_model_settings
from app.services.queries import get_current_snapshot


_FUND_CODE_RE = re.compile(r"(?<![A-Z0-9])[A-Z]{3}\d{3}(?![A-Z0-9])")
_CATEGORY_ALIASES = {
    "权益": "权益",
    "股票": "权益",
    "固收": "固收",
    "债券": "固收",
    "货币": "货币",
    "货币市场": "货币",
    "混合": "混合",
    "商品": "商品",
}


def _now() -> datetime:
    """返回带时区的当前时间；参数为空。"""

    return datetime.now(timezone.utc)


def _fund_codes(question: str, requested: list[str]) -> list[str]:
    """合并问题和请求中的基金代码；参数 question/requested 为原始问题和显式代码。"""

    values = [item.upper().strip() for item in requested if item.strip()]
    values.extend(_FUND_CODE_RE.findall(question.upper()))
    return list(dict.fromkeys(values))


def classify_intent(question: str, task_target: str | None = None) -> AgentIntent:
    """识别受控 Agent 意图；参数 question/task_target 为问题和显式任务目标。"""

    envelope = classify_intent_envelope(question, task_target)
    return {
        "plan_supplement_source": "supplementary_source",
        "plan_semiannual_job": "create_semiannual_job",
        "plan_monthly_job": "create_monthly_job",
        "compare_funds": "compare",
    }.get(envelope.intent, "query")


def _category_from_question(question: str) -> str | None:
    """从问题提取单一资产类别；参数 question 为自然语言问题。"""

    for token, category in _CATEGORY_ALIASES.items():
        if token in question:
            return category
    return None


def _scope(
    session: Session,
    request: AgentPlanRequest,
    question: str,
) -> tuple[dict, int, list[str]]:
    """冻结计划范围预览；参数 session/request/question 为会话、计划请求和问题。"""

    codes = _fund_codes(question, request.fund_codes)
    limitations: list[str] = []
    if request.collection_id:
        collection = session.get(FundCollection, request.collection_id)
        if collection is None or collection.status != "active" or collection.expires_at <= _now():
            raise ApiError(422, "E_COLLECTION_EXPIRED", "基金集合不存在、已过期或已清理")
        if request.collection_session_key and collection.session_key != request.collection_session_key:
            raise ApiError(403, "E_COLLECTION_SESSION", "基金集合不属于当前会话")
        count = int(
            session.scalar(
                select(func.count()).select_from(FundCollectionItem).where(
                    FundCollectionItem.collection_id == collection.id
                )
            )
            or 0
        )
        return (
            {
                "mode": ScopeMode.COLLECTION.value,
                "collection_id": str(collection.id),
                "collection_session_key": collection.session_key,
            },
            count,
            limitations,
        )
    if codes:
        funds = list(session.scalars(select(Fund).where(Fund.fund_code.in_(codes))))
        found = {fund.fund_code for fund in funds}
        missing = [code for code in codes if code not in found]
        if missing:
            raise ApiError(422, "E_AGENT_FUND_NOT_FOUND", f"基金代码不存在：{', '.join(missing)}")
        return (
            {"mode": ScopeMode.FUNDS.value, "fund_codes": codes},
            len(funds),
            limitations,
        )
    category = _category_from_question(question)
    if category:
        count = int(
            session.scalar(
                select(func.count(func.distinct(Fund.id)))
                .join(FundClassification, FundClassification.fund_id == Fund.id)
                .where(FundClassification.level_1 == category)
            )
            or 0
        )
        return ({"mode": ScopeMode.CATEGORY.value, "categories": [category]}, count, limitations)
    if any(token in question for token in ("管理人", "公司")):
        limitations.append("manager_scope_requires_explicit_selection")
    count = int(session.scalar(select(func.count()).select_from(Fund)) or 0)
    limitations.append("scope_defaulted_to_all_funds")
    return ({"mode": ScopeMode.ALL.value}, count, limitations)


def _model_policy(provider: str | None, intent: AgentIntent) -> dict:
    """构造任务模型策略；参数 provider/intent 为模型选择和 Agent 意图。"""

    if intent not in {"create_monthly_job", "create_semiannual_job"}:
        return {}
    settings = read_model_settings()
    selected = provider or settings.structured_provider
    policy = {"structured_provider": selected}
    if selected == "local":
        policy.update(
            {
                "local_model": settings.local.primary_model,
                "local_workers": settings.local.workers,
            }
        )
    return policy


def _estimate(count: int, intent: AgentIntent, provider: str) -> dict[str, int]:
    """估算任务分钟区间；参数 count/intent/provider 为基金数、目标和模型。"""

    if intent not in {"create_monthly_job", "create_semiannual_job"} or count <= 0:
        return {"minimum": 0, "maximum": 0}
    parse_min = max(2, ((count + 35) // 36) * 2)
    parse_max = max(5, ((count + 17) // 18) * 5)
    if intent == "create_semiannual_job":
        parse_min *= 2
        parse_max *= 2
    if provider == "local":
        structure_min = max(3, count * 3)
        structure_max = max(6, count * 6)
    else:
        structure_min = max(1, (count + 7) // 8)
        structure_max = max(2, (count + 2) // 3)
    return {"minimum": parse_min + structure_min, "maximum": parse_max + structure_max}


def _append_message(session: Session, run_id: uuid.UUID, role: str, content: str) -> None:
    """追加 Agent 消息；参数 session/run_id/role/content 为消息上下文。"""

    sequence = int(
        session.scalar(
            select(func.max(AgentMessage.sequence_no)).where(AgentMessage.agent_run_id == run_id)
        )
        or 0
    ) + 1
    session.add(
        AgentMessage(
            id=uuid.uuid4(),
            agent_run_id=run_id,
            role=role,
            content=content,
            sequence_no=sequence,
        )
    )
    session.flush()


def _to_data(run: AgentRun) -> AgentPlanData:
    """将 AgentRun 转为计划响应；参数 run 为数据库 Agent 运行。"""

    plan = run.plan_json or {}
    return AgentPlanData(
        run_id=run.id,
        snapshot_id=run.snapshot_id,
        status=run.status,
        intent=plan.get("intent", "query"),
        intent_envelope=plan.get("intent_envelope"),
        question=run.question,
        plan=plan,
        scope_definition=plan.get("scope_definition", {}),
        scope_count=int(plan.get("scope_count", 0)),
        task_target=plan.get("task_target"),
        model_policy=plan.get("model_policy", {}),
        estimated_minutes=plan.get("estimated_minutes", {}),
        impact_summary=plan.get("impact_summary", []),
        limitations=plan.get("limitations", []),
        requires_confirmation=bool(plan.get("requires_confirmation", False)),
        confirmation_expires_at=run.started_at + timedelta(minutes=30) if run.started_at else None,
        job_id=plan.get("job_id"),
    )


def create_plan(session: Session, request: AgentPlanRequest) -> AgentPlanData:
    """生成只读或待确认计划；参数 session/request 为数据库会话和计划请求。"""

    snapshot = get_current_snapshot(session)
    envelope = classify_intent_envelope(request.question, request.task_target)
    intent = {
        "plan_supplement_source": "supplementary_source",
        "plan_semiannual_job": "create_semiannual_job",
        "plan_monthly_job": "create_monthly_job",
        "compare_funds": "compare",
    }.get(envelope.intent, "query")
    if envelope.needs_clarification:
        scope_definition, scope_count, limitations = {"mode": "unknown"}, 0, [
            envelope.clarification_question or "需要补充问题范围。"
        ]
    else:
        scope_definition, scope_count, limitations = _scope(session, request, request.question)
    task_target = None
    if intent == "create_monthly_job":
        task_target = TaskTarget.FUND_MONTHLY.value
    elif intent == "create_semiannual_job":
        task_target = TaskTarget.FUND_SEMIANNUAL.value
    provider = request.structured_provider or read_model_settings().structured_provider
    model_policy = _model_policy(request.structured_provider, intent)
    requires_confirmation = intent in {"create_monthly_job", "create_semiannual_job"}
    if intent == "supplementary_source":
        limitations.append("supplementary_source_requires_url_and_fund_detail_entry")
    estimate = _estimate(scope_count, intent, provider)
    impact = [f"范围冻结为 {scope_count} 只基金。"]
    if requires_confirmation:
        impact.extend(
            [
                f"将创建{('月报' if task_target == TaskTarget.FUND_MONTHLY.value else '半年报')}下载、解析、结构化任务。",
                "用户确认前不会下载、解析、结构化或写入任务队列。",
                "任务创建后将进入现有全局写锁和 Worker 状态机。",
            ]
        )
    else:
        impact.append("这是只读计划，不会创建任务或修改数据库业务数据。")
    plan = {
        "intent": intent,
        "scope_definition": scope_definition,
        "scope_count": scope_count,
        "task_target": task_target,
        "stages": [stage.value for stage in (StageName.DOWNLOAD, StageName.PARSE, StageName.STRUCTURE)] if requires_confirmation else [],
        "model_policy": model_policy,
        "estimated_minutes": estimate,
        "impact_summary": impact,
        "limitations": list(dict.fromkeys(limitations)),
        "requires_confirmation": requires_confirmation,
    }
    status = (
        "needs_clarification"
        if envelope.needs_clarification
        else "awaiting_confirmation"
        if requires_confirmation
        else "read_only"
    )
    plan["intent_envelope"] = envelope.model_dump(mode="json")
    run = AgentRun(
        id=uuid.uuid4(),
        snapshot_id=snapshot.id,
        question=request.question,
        route="controlled_plan",
        status=status,
        plan_json=plan,
        response_schema_version="agent-plan-v2",
        started_at=_now(),
    )
    session.add(run)
    session.flush()
    _append_message(session, run.id, "user", request.question)
    _append_message(session, run.id, "assistant", "已生成受控 Agent 计划，写操作需要用户确认。" if requires_confirmation else "已生成只读计划，不会执行写操作。")
    session.add(
        ToolCall(
            id=uuid.uuid4(),
            agent_run_id=run.id,
            tool_name="agent.plan",
            tool_version="v1",
            input_json=request.model_dump(mode="json"),
            output_json=plan,
            status="awaiting_confirmation" if requires_confirmation else "read_only",
            requires_confirmation=requires_confirmation,
        )
    )
    if envelope.classifier == "local_qwen":
        session.add(
            ModelCall(
                id=uuid.uuid4(),
                agent_run_id=run.id,
                provider="local",
                model_name=envelope.classifier_model or "qwen3:4b",
                model_version="intent-v1",
                input_tokens=0,
                output_tokens=0,
                latency_ms=envelope.classifier_latency_ms,
                status="succeeded",
            )
        )
    session.commit()
    session.refresh(run)
    return _to_data(run)


def get_plan(session: Session, run_id: uuid.UUID) -> AgentPlanData:
    """读取 Agent 计划；参数 session/run_id 为数据库会话和运行 ID。"""

    run = session.get(AgentRun, run_id)
    if run is None:
        raise ApiError(404, "E_AGENT_RUN_NOT_FOUND", "Agent 运行不存在")
    return _to_data(run)


def confirm_plan(
    session: Session,
    run_id: uuid.UUID,
    request: AgentConfirmRequest,
) -> AgentPlanData:
    """确认或拒绝任务计划；参数 session/run_id/request 为会话、运行 ID 和确认请求。"""

    run = session.get(AgentRun, run_id)
    if run is None:
        raise ApiError(404, "E_AGENT_RUN_NOT_FOUND", "Agent 运行不存在")
    if run.status != "awaiting_confirmation":
        raise ApiError(409, "E_AGENT_CONFIRMATION_STATE", "该计划不在待确认状态")
    tool = session.scalar(
        select(ToolCall)
        .where(ToolCall.agent_run_id == run.id, ToolCall.tool_name == "agent.plan")
        .order_by(ToolCall.created_at.desc())
    )
    if not request.confirmed:
        run.status = "rejected"
        if tool:
            tool.status = "rejected"
            tool.confirmed_at = _now()
        _append_message(session, run.id, "user", "拒绝执行计划")
        _append_message(session, run.id, "assistant", "计划已拒绝，未创建任务。")
        session.commit()
        session.refresh(run)
        return _to_data(run)
    plan = run.plan_json or {}
    scope = plan.get("scope_definition") or {}
    try:
        job_request = JobCreateRequest(
            scope=JobScopeRequest(**scope),
            stages=[StageName(item) for item in plan.get("stages") or []],
            document_policy={"prefer": "semiannual" if plan.get("task_target") == TaskTarget.FUND_SEMIANNUAL.value else "monthly"},
            model_policy=plan.get("model_policy") or {},
            task_target=TaskTarget(plan["task_target"]),
        )
        job, _ = create_job(session, job_request, idempotency_key=f"agent-{run.id}")
    except ApiError as exc:
        run.status = "failed"
        if tool:
            tool.status = "failed"
            tool.error_code = exc.code
            tool.error_message = exc.message
        _append_message(session, run.id, "assistant", f"任务创建失败：{exc.message}")
        session.commit()
        raise
    run.status = "confirmed"
    plan = {**plan, "job_id": str(job.id)}
    run.plan_json = plan
    if tool:
        tool.status = "succeeded"
        tool.output_json = {"job_id": str(job.id), "status": job.status}
        tool.confirmed_at = _now()
    _append_message(session, run.id, "user", "确认执行计划")
    _append_message(session, run.id, "assistant", f"已创建任务 {job.id}，后续由现有 Worker 执行。")
    session.commit()
    session.refresh(run)
    return _to_data(run)


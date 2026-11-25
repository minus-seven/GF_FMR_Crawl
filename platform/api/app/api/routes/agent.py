"""受控 Agent 计划和确认接口。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.responses import envelope as api_envelope
from app.schemas.agent import AgentConfirmRequest, AgentPlanData, AgentPlanRequest
from app.schemas.intent import IntentRecognitionData, IntentRecognitionRequest
from app.schemas.common import ApiResponse
from app.services.agent import confirm_plan, create_plan, get_plan
from app.services.intent import candidate_intents, classify_intent_envelope, route_intent


router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/intent", response_model=ApiResponse[IntentRecognitionData])
def recognize_intent(
    body: IntentRecognitionRequest,
    request: Request,
) -> ApiResponse[IntentRecognitionData]:
    """识别意图并返回受控路由；参数 body/request 为请求和 API 上下文。"""

    intent_envelope = classify_intent_envelope(
        body.question,
        body.task_target,
        allow_local_model=body.allow_local_model,
    )
    data = IntentRecognitionData(
        envelope=intent_envelope,
        candidates=candidate_intents(body.question, body.task_target),
        route=route_intent(intent_envelope),
    )
    return api_envelope(request, data, None)


@router.post("/plan", response_model=ApiResponse[AgentPlanData])
def plan(
    body: AgentPlanRequest,
    request: Request,
    session: Session = Depends(get_db),
) -> ApiResponse[AgentPlanData]:
    """生成只读或待确认的 Agent 计划；参数 body/request/session 为请求和数据库上下文。"""

    data = create_plan(session, body)
    return api_envelope(request, data, data.snapshot_id)


@router.get("/runs/{run_id}", response_model=ApiResponse[AgentPlanData])
def run_detail(
    run_id: UUID,
    request: Request,
    session: Session = Depends(get_db),
) -> ApiResponse[AgentPlanData]:
    """读取 Agent 计划；参数 run_id/request/session 为运行 ID 和数据库上下文。"""

    data = get_plan(session, run_id)
    return api_envelope(request, data, data.snapshot_id)


@router.post("/runs/{run_id}/confirm", response_model=ApiResponse[AgentPlanData])
def confirm(
    run_id: UUID,
    body: AgentConfirmRequest,
    request: Request,
    session: Session = Depends(get_db),
) -> ApiResponse[AgentPlanData]:
    """确认或拒绝 Agent 计划；参数 run_id/body/request/session 为运行、确认和数据库上下文。"""

    data = confirm_plan(session, run_id, body)
    return api_envelope(request, data, data.snapshot_id)


"""SQL 优先问答 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.errors import ApiError
from app.api.responses import envelope
from app.schemas.chat import ChatRequest, ChatResponseData
from app.schemas.common import ApiResponse
from app.services.chat import answer_question


router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ApiResponse[ChatResponseData])
def chat(
    body: ChatRequest,
    request: Request,
    session: Session = Depends(get_db),
) -> ApiResponse[ChatResponseData]:
    """执行 SQL 优先问答并返回证据契约；参数 body/request/session 为请求和上下文。"""

    try:
        data = answer_question(session, body)
    except LookupError as exc:
        raise ApiError(
            503,
            "E_KNOWLEDGE_VECTOR_UNAVAILABLE",
            str(exc),
            retryable=False,
        ) from exc
    except ValueError as exc:
        raise ApiError(
            422,
            "E_KNOWLEDGE_QUERY_INVALID",
            str(exc),
            retryable=False,
        ) from exc
    except PermissionError as exc:
        raise ApiError(
            403,
            "E_KNOWLEDGE_SNAPSHOT_NOT_PUBLISHED",
            str(exc),
            retryable=False,
        ) from exc
    return envelope(request, data, data.snapshot_id)

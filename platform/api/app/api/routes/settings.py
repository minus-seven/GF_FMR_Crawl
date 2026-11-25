"""External model settings endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.api.responses import envelope
from app.schemas.common import ApiResponse
from app.schemas.settings import LocalModelHealthData, ModelSettingsData, ModelSettingsUpdate
from app.services.model_settings import (
    check_local_model_runtime,
    read_model_settings,
    update_model_settings,
)


router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/models", response_model=ApiResponse[ModelSettingsData])
def model_settings(
    request: Request,
    response: Response,
) -> ApiResponse[ModelSettingsData]:
    """读取本机模型设置；参数 request/response 为请求上下文。"""

    response.headers["Cache-Control"] = "no-store"
    return envelope(request, read_model_settings(), None)


@router.put("/models", response_model=ApiResponse[ModelSettingsData])
def save_model_settings(
    body: ModelSettingsUpdate,
    request: Request,
    response: Response,
) -> ApiResponse[ModelSettingsData]:
    """保存模型设置；参数 body/request/response 为更新内容和请求上下文。"""

    response.headers["Cache-Control"] = "no-store"
    return envelope(request, update_model_settings(body), None)


@router.get("/models/local-health", response_model=ApiResponse[LocalModelHealthData])
def local_model_health(
    request: Request,
    response: Response,
) -> ApiResponse[LocalModelHealthData]:
    """检查本地模型服务是否可用；参数 request/response 为请求上下文。"""

    response.headers["Cache-Control"] = "no-store"
    return envelope(request, check_local_model_runtime(), None)

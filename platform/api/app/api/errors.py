"""Stable API error types and FastAPI exception handlers."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """Structured application error returned to API clients.

    参数 status_code：HTTP 状态码；code：稳定错误码；message：中文说明；
    retryable：调用方能否重试；details：附加审计信息。
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


def request_id(request: Request) -> str:
    """读取请求追踪 ID；参数 request：当前请求。"""

    return getattr(request.state, "request_id", "unknown")


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    """把 ApiError 转换为统一错误信封；参数 request/exc 为请求和异常。"""

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
                "details": exc.details,
            },
            "request_id": request_id(request),
        },
    )


async def validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """把参数校验失败转换为稳定错误码；参数 request/exc 为请求和异常。"""

    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "E_VALIDATION",
                "message": "请求参数不符合契约",
                "retryable": False,
                "details": {"errors": exc.errors()},
            },
            "request_id": request_id(request),
        },
    )


async def http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """处理未预期异常并隐藏内部细节；参数 request/exc 为请求和异常。"""

    status = HTTPStatus.INTERNAL_SERVER_ERROR
    return JSONResponse(
        status_code=status.value,
        content={
            "error": {
                "code": "E_INTERNAL",
                "message": "服务内部错误",
                "retryable": True,
                "details": {},
            },
            "request_id": request_id(request),
        },
    )


def install_exception_handlers(app: FastAPI) -> None:
    """注册统一异常处理；参数 app：FastAPI 应用。"""

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, http_error_handler)

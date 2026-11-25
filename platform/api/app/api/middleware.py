"""Request correlation and response headers."""

from __future__ import annotations

from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach an X-Request-Id to every request and response."""

    async def dispatch(self, request: Request, call_next):
        """处理单个请求并传播 request_id；参数 request/call_next 为请求和下游。"""

        request_id = request.headers.get("X-Request-Id") or f"req_{uuid4().hex}"
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

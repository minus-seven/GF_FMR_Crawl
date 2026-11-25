"""FastAPI dependency providers."""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from fastapi import Request
from sqlalchemy.orm import Session

from app.db.session import create_session_factory


@lru_cache(maxsize=1)
def get_session_factory():
    """创建并缓存应用 Session 工厂；参数为空。"""

    return create_session_factory()


def get_db() -> Iterator[Session]:
    """提供只读查询 Session；参数为空。"""

    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def get_request_id(request: Request) -> str:
    """返回中间件写入的请求追踪 ID；参数 request：当前请求。"""

    return getattr(request.state, "request_id", "unknown")

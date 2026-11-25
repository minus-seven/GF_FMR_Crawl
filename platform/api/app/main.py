"""FastAPI entry point for deterministic fund research queries."""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy import delete

from app.api.errors import install_exception_handlers
from app.api.middleware import RequestContextMiddleware
from app.api.routes import (
    agent,
    analytics,
    chat,
    funds,
    health,
    jobs,
    knowledge,
    quality,
    settings,
    snapshots,
)
from app.db.session import create_session_factory
from app.models import FundCollection


app = FastAPI(
    title="Fund Research Knowledge Agent API",
    version="0.4.0",
    description="Fund queries, controlled tasks and page-level knowledge search API.",
)
app.add_middleware(RequestContextMiddleware)
install_exception_handlers(app)
app.include_router(health.router, prefix="/api")
app.include_router(snapshots.router, prefix="/api")
app.include_router(funds.router, prefix="/api")
app.include_router(quality.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(knowledge.router, prefix="/api")
app.include_router(agent.router, prefix="/api")


@app.on_event("startup")
def clear_temporary_collections() -> None:
    """服务重启时清理临时基金集合；参数为空。"""

    session = create_session_factory()()
    try:
        session.execute(delete(FundCollection))
        session.commit()
    finally:
        session.close()


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    """返回 API 入口提示；参数为空。"""

    return {"name": "Fund Research Knowledge Agent API", "docs": "/docs"}

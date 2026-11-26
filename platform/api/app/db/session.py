"""Database engine and session factory."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def create_db_engine(database_url: str | None = None):
    """创建 PostgreSQL Engine。

    参数 database_url：可选连接地址；为空时读取环境配置。
    返回：配置了预连接检查的 SQLAlchemy Engine。
    """

    url = database_url or get_settings().database_url
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=int(os.environ.get("PLATFORM_DB_POOL_SIZE", "10")),
        max_overflow=int(os.environ.get("PLATFORM_DB_MAX_OVERFLOW", "10")),
        pool_timeout=float(os.environ.get("PLATFORM_DB_POOL_TIMEOUT", "5")),
        future=True,
    )


def create_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    """创建 Session 工厂；参数 database_url：可选连接地址。"""

    return sessionmaker(
        bind=create_db_engine(database_url),
        expire_on_commit=False,
        autoflush=False,
        future=True,
    )


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """在事务中提供一个 Session；参数 factory：Session 工厂。"""

    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

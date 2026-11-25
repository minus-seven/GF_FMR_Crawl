"""Create the D1 core PostgreSQL schema.

Revision ID: 20261022_0001
Revises:
Create Date: 2026-10-22
"""

from __future__ import annotations

from alembic import op

from app.db.base import Base
from app.models import registry as model_registry  # noqa: F401


revision = "20261022_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Enable pgvector and create all D1 core tables."""

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    """Drop D1 core tables while retaining the pgvector extension."""

    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)

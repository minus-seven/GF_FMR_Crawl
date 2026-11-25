"""Scope document chunk identity to the published snapshot.

Revision ID: 20261027_0007
Revises: 20261027_0006
Create Date: 2026-10-27
"""

from alembic import op


revision = "20261027_0007"
down_revision = "20261027_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """允许同一不可变文档在各发布快照中拥有独立切片。"""

    op.drop_constraint(
        "uq_document_chunk_index",
        "document_chunks",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_document_chunk_snapshot_index",
        "document_chunks",
        ["snapshot_id", "document_version_id", "chunk_index"],
    )


def downgrade() -> None:
    """恢复文档版本的全局切片唯一键。"""

    op.drop_constraint(
        "uq_document_chunk_snapshot_index",
        "document_chunks",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_document_chunk_index",
        "document_chunks",
        ["document_version_id", "chunk_index"],
    )

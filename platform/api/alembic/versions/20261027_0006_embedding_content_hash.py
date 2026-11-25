"""Track the source chunk hash on every embedding.

Revision ID: 20261027_0006
Revises: 20261027_0005
Create Date: 2026-10-27
"""

from alembic import op
import sqlalchemy as sa


revision = "20261027_0006"
down_revision = "20261027_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加向量内容哈希并回填已有向量记录。"""

    op.add_column(
        "chunk_embeddings",
        sa.Column("content_hash", sa.String(64), nullable=True),
    )
    op.execute(
        """
        UPDATE chunk_embeddings embedding
        SET content_hash = chunk.content_hash
        FROM document_chunks chunk
        WHERE chunk.id = embedding.chunk_id
        """
    )
    op.alter_column(
        "chunk_embeddings",
        "content_hash",
        nullable=False,
    )


def downgrade() -> None:
    """移除向量内容哈希。"""

    op.drop_column("chunk_embeddings", "content_hash")

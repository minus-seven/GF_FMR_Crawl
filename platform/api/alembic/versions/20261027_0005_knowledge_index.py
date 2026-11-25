"""Add page-level knowledge index and Agent audit schemas.

Revision ID: 20261027_0005
Revises: 20261023_0004
Create Date: 2026-10-27
"""

from alembic import op
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261027_0005"
down_revision = "20261023_0004"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    """返回平台统一时间戳列；参数为空。"""

    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    """创建页级切片、向量、索引运行和 Agent 审计表。"""

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "parse_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        *_timestamps(),
        sa.Column(
            "document_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("parser_name", sa.String(80), nullable=False),
        sa.Column("parser_version", sa.String(120), nullable=False),
        sa.Column("artifact_path", sa.Text()),
        sa.Column("page_mapping_tier", sa.String(80)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "document_version_id",
            "parser_name",
            "parser_version",
            name="uq_parse_run_document_parser",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_parse_runs_status",
        ),
    )

    op.create_table(
        "document_pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        *_timestamps(),
        sa.Column(
            "document_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("width", sa.Numeric(12, 4)),
        sa.Column("height", sa.Numeric(12, 4)),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "document_version_id",
            "page_number",
            name="uq_document_page_number",
        ),
        sa.CheckConstraint("page_number > 0", name="ck_document_pages_number"),
    )
    op.create_index(
        "ix_document_pages_document_page",
        "document_pages",
        ["document_version_id", "page_number"],
    )

    op.create_table(
        "document_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        *_timestamps(),
        sa.Column(
            "document_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "fund_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("funds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dataset_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("report_period", sa.Date(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("section_title", sa.Text()),
        sa.Column("chunk_type", sa.String(40), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("citation_tier", sa.String(80), nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('simple', coalesce(section_title, '') || ' ' || content)",
                persisted=True,
            ),
        ),
        sa.UniqueConstraint(
            "document_version_id",
            "chunk_index",
            name="uq_document_chunk_index",
        ),
        sa.CheckConstraint("page_number > 0", name="ck_document_chunks_page"),
    )
    op.create_index(
        "ix_document_chunks_snapshot_fund_period",
        "document_chunks",
        ["snapshot_id", "fund_id", "report_period"],
    )
    op.create_index(
        "ix_document_chunks_document_page",
        "document_chunks",
        ["document_version_id", "page_number"],
    )
    op.create_index(
        "ix_document_chunks_content_trgm",
        "document_chunks",
        ["content"],
        postgresql_using="gin",
        postgresql_ops={"content": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_document_chunks_section_trgm",
        "document_chunks",
        ["section_title"],
        postgresql_using="gin",
        postgresql_ops={"section_title": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_document_chunks_search_vector",
        "document_chunks",
        ["search_vector"],
        postgresql_using="gin",
    )

    op.create_table(
        "chunk_embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        *_timestamps(),
        sa.Column(
            "chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_chunks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_name", sa.String(160), nullable=False),
        sa.Column("model_version", sa.String(160), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.UniqueConstraint(
            "chunk_id",
            "model_name",
            "model_version",
            name="uq_chunk_embedding_model",
        ),
    )
    op.create_index(
        "ix_chunk_embeddings_vector_hnsw",
        "chunk_embeddings",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "knowledge_index_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        *_timestamps(),
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dataset_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("scope_hash", sa.String(64), nullable=False),
        sa.Column("index_version", sa.String(80), nullable=False),
        sa.Column("parser_version", sa.String(120), nullable=False),
        sa.Column("embedding_model", sa.String(160)),
        sa.Column("embedding_version", sa.String(160)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "counts_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "snapshot_id",
            "scope_hash",
            "index_version",
            name="uq_knowledge_index_run_scope",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_knowledge_index_runs_status",
        ),
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        *_timestamps(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dataset_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("route", sa.String(80)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "plan_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("response_schema_version", sa.String(40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "agent_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        *_timestamps(),
        sa.Column(
            "agent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "agent_run_id",
            "sequence_no",
            name="uq_agent_messages_sequence",
        ),
    )

    op.create_table(
        "tool_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        *_timestamps(),
        sa.Column(
            "agent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(160), nullable=False),
        sa.Column("tool_version", sa.String(80), nullable=False),
        sa.Column(
            "input_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "output_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("latency_ms", sa.BigInteger()),
        sa.Column(
            "requires_confirmation",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "model_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        *_timestamps(),
        sa.Column(
            "agent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
        ),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model_name", sa.String(160), nullable=False),
        sa.Column("model_version", sa.String(160)),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "cache_hit_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "cache_miss_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("estimated_cost", sa.Numeric(18, 8)),
        sa.Column("latency_ms", sa.BigInteger()),
        sa.Column("status", sa.String(32), nullable=False),
    )

    op.create_table(
        "citations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        *_timestamps(),
        sa.Column(
            "agent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("claim_id", sa.String(120), nullable=False),
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dataset_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "fund_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("funds.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "document_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("excerpt_sha256", sa.String(64), nullable=False),
        sa.Column("citation_tier", sa.String(80), nullable=False),
        sa.Column("validation_status", sa.String(40), nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint("page_number > 0", name="ck_citations_page_number"),
    )
    op.create_index(
        "ix_citations_agent_run",
        "citations",
        ["agent_run_id", "claim_id"],
    )

    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        *_timestamps(),
        sa.Column(
            "agent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dataset_batches.id", ondelete="SET NULL"),
        ),
        sa.Column("artifact_type", sa.String(80), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("template_version", sa.String(80)),
        sa.Column("status", sa.String(32), nullable=False),
    )

    op.create_table(
        "export_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        *_timestamps(),
        sa.Column(
            "agent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
        ),
        sa.Column("artifact_type", sa.String(80), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "request_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
        ),
        sa.Column("error_message", sa.Text()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "agent_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        *_timestamps(),
        sa.Column(
            "agent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column(
            "payload_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.UniqueConstraint(
            "agent_run_id",
            "sequence_no",
            name="uq_agent_events_sequence",
        ),
    )


def downgrade() -> None:
    """删除知识索引与 Agent 审计表，保留共享 pg_trgm 扩展。"""

    op.drop_table("agent_events")
    op.drop_table("export_jobs")
    op.drop_table("artifacts")
    op.drop_index("ix_citations_agent_run", table_name="citations")
    op.drop_table("citations")
    op.drop_table("model_calls")
    op.drop_table("tool_calls")
    op.drop_table("agent_messages")
    op.drop_table("agent_runs")
    op.drop_table("knowledge_index_runs")
    op.drop_index(
        "ix_chunk_embeddings_vector_hnsw",
        table_name="chunk_embeddings",
    )
    op.drop_table("chunk_embeddings")
    op.drop_index(
        "ix_document_chunks_search_vector",
        table_name="document_chunks",
    )
    op.drop_index(
        "ix_document_chunks_section_trgm",
        table_name="document_chunks",
    )
    op.drop_index(
        "ix_document_chunks_content_trgm",
        table_name="document_chunks",
    )
    op.drop_index(
        "ix_document_chunks_document_page",
        table_name="document_chunks",
    )
    op.drop_index(
        "ix_document_chunks_snapshot_fund_period",
        table_name="document_chunks",
    )
    op.drop_table("document_chunks")
    op.drop_index(
        "ix_document_pages_document_page",
        table_name="document_pages",
    )
    op.drop_table("document_pages")
    op.drop_table("parse_runs")

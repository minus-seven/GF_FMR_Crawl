"""Knowledge search request and response schemas."""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class KnowledgeSearchItem(BaseModel):
    """One citation-ready search result."""

    chunk_id: UUID
    fund_id: UUID
    fund_code: str
    fund_name: str
    report_period: date
    document_version_id: UUID
    page_number: int
    section_title: str | None
    chunk_type: str
    citation_tier: str
    content: str
    source_path: str
    score: float
    content_hash: str


class KnowledgeSearchData(BaseModel):
    """Knowledge search result envelope data."""

    query: str | None
    mode: Literal["fulltext", "vector", "hybrid"]
    vector_available: bool
    limitations: list[str] = Field(default_factory=list)
    items: list[KnowledgeSearchItem]


class KnowledgeIndexStatusData(BaseModel):
    """Published knowledge index quality and runtime status."""

    snapshot_id: UUID
    index_status: str | None
    index_version: str | None
    parser_version: str | None
    documents: int
    funds: int
    pages: int
    chunks: int
    embeddings: int
    incomplete_chunks: int
    citation_tiers: dict[str, int]
    vector_model: str | None
    vector_version: str | None

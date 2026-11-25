"""SQL 优先问答与证据契约。"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


ChatStatus = Literal["answered", "insufficient_evidence", "evidence_only"]
ChatRoute = Literal["sql", "sql+knowledge", "knowledge_search"]


class ChatRequest(BaseModel):
    """问答请求；参数为问题、基金范围、报告期和检索模式。"""

    question: str = Field(min_length=1, max_length=2000)
    fund_codes: list[str] = Field(default_factory=list, max_length=100)
    report_period: date | None = None
    snapshot_id: UUID | None = None
    mode: Literal["fulltext", "vector", "hybrid"] = "hybrid"
    query_vector: list[float] = Field(default_factory=list, max_length=1024)
    limit: int = Field(default=8, ge=1, le=20)


class ChatEvidence(BaseModel):
    """可审计证据；结构化记录不伪造 PDF 页码。"""

    evidence_id: str
    evidence_type: Literal["structured_record", "document_chunk"]
    supports: str
    fund_code: str
    fund_name: str
    report_period: date | None
    document_version_id: UUID | None
    page_number: int | None
    section_title: str | None = None
    citation_tier: str | None = None
    source_label: str | None = None
    source_path: str | None = None
    source_url: str | None = None
    content: str
    content_hash: str | None = None


class ChatStructuredValue(BaseModel):
    """SQL 直接命中的结构化值。"""

    fund_code: str
    fund_name: str
    report_period: date
    field: str
    value: float | str | None
    unit: str | None
    review_status: str | None = None
    quality_status: str
    document_version_id: UUID


class ChatClaim(BaseModel):
    """回答中的一条可追溯事实声明。"""

    claim_id: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class ChatResponseData(BaseModel):
    """问答结果；每条事实声明必须能回指证据。"""

    question: str
    status: ChatStatus
    route: ChatRoute
    answer: str
    snapshot_id: UUID
    structured_values: list[ChatStructuredValue] = Field(default_factory=list)
    claims: list[ChatClaim] = Field(default_factory=list)
    evidence: list[ChatEvidence] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

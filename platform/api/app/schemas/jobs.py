"""Task submission, progress and event schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class ScopeMode(str, Enum):
    """Supported deterministic task scope selectors."""

    ALL = "all"
    MANAGER = "manager"
    CATEGORY = "category"
    FUNDS = "funds"
    TAG = "tag"
    COLLECTION = "collection"


class TaskTarget(str, Enum):
    """任务下载和结构化目标。"""

    FUND_MONTHLY = "fund_monthly"
    FUND_SEMIANNUAL = "fund_semiannual"
    MASTER_DATABASE = "master_database"


class StageName(str, Enum):
    """Whitelisted worker stages."""

    DOWNLOAD = "download"
    PARSE = "parse"
    STRUCTURE = "structure"


class JobScopeRequest(BaseModel):
    """Task scope frozen when the job is created."""

    mode: ScopeMode
    manager_ids: list[UUID] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    fund_codes: list[str] = Field(default_factory=list)
    tag_filters: list[str] = Field(default_factory=list)
    collection_id: UUID | None = None
    collection_session_key: str | None = None

    @model_validator(mode="after")
    def validate_selector(self):
        """验证每种范围模式所需参数；参数为当前对象。"""

        if self.mode == ScopeMode.MANAGER and not self.manager_ids:
            raise ValueError("manager 模式需要 manager_ids")
        if self.mode == ScopeMode.CATEGORY and not self.categories:
            raise ValueError("category 模式需要 categories")
        if self.mode == ScopeMode.FUNDS and not self.fund_codes:
            raise ValueError("funds 模式需要 fund_codes")
        if self.mode == ScopeMode.TAG and not self.tag_filters:
            raise ValueError("tag 模式需要 tag_filters")
        if self.mode == ScopeMode.COLLECTION and self.collection_id is None:
            raise ValueError("collection 模式需要 collection_id")
        return self


class ManagerOptionData(BaseModel):
    """管理人范围选项。"""

    manager_id: UUID
    manager_name: str
    fund_count: int


class CategoryOptionData(BaseModel):
    """资产类别范围选项。"""

    category: str
    fund_count: int


class ScopeOptionsData(BaseModel):
    """任务中心可用的确定性范围选项。"""

    total_funds: int
    managers: list[ManagerOptionData]
    categories: list[CategoryOptionData]


class MasterDatabaseOptionsData(BaseModel):
    """综合资料库任务的可用参考文件与旧爬虫状态。"""

    default_reference_file: str
    reference_files: list[str] = Field(default_factory=list)
    default_sheet: str = "主数据"
    default_rename_sheet: str = "改名"
    crawler_root: str | None = None
    crawler_available: bool


class MasterDatabaseImportData(BaseModel):
    """综合资料库落库结果。"""

    job_id: UUID
    workbook_path: str
    summary: dict[str, Any]


class JobCreateRequest(BaseModel):
    """Create-job request body."""

    scope: JobScopeRequest
    stages: list[StageName] = Field(min_length=1)
    input_job_id: UUID | None = None
    document_policy: dict[str, Any] = Field(default_factory=dict)
    model_policy: dict[str, Any] = Field(default_factory=dict)
    task_target: TaskTarget = TaskTarget.FUND_MONTHLY

    @model_validator(mode="after")
    def normalize_and_validate_stages(self):
        """按下载、解析、结构化顺序规范化阶段；参数为当前请求。"""

        order = [StageName.DOWNLOAD, StageName.PARSE, StageName.STRUCTURE]
        selected = set(self.stages)
        self.stages = [stage for stage in order if stage in selected]
        names = {stage.value for stage in self.stages}
        if (
            "download" in names
            and "structure" in names
            and "parse" not in names
        ):
            raise ValueError(
                "同时执行下载和结构化时必须包含解析阶段"
            )
        policy = self.model_policy
        if policy:
            provider = policy.get("structured_provider")
            if provider not in {"deepseek", "qwen", "local"}:
                raise ValueError("model_policy.structured_provider 不受支持")
            if "structure" not in names:
                raise ValueError("仅结构化任务可以指定 model_policy")
            if provider == "local":
                # 本地模型由“模型设置”统一配置；任务只需选择 local。
                # 兼容旧客户端显式传入 local_model，但不再强制输入。
                if "local_model" in policy and policy.get("local_model") is not None:
                    model = str(policy.get("local_model") or "").strip()
                    if not model:
                        raise ValueError("local_model 不能为空")
                workers = policy.get("local_workers", 1)
                if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 2:
                    raise ValueError("local_workers 必须是 1 或 2")
        return self


class JobProgress(BaseModel):
    """Aggregate progress across all requested stages."""

    total: int
    processed: int
    succeeded: int
    skipped: int
    failed: int


class JobStepData(BaseModel):
    """One stage summary."""

    step_type: str
    status: str
    total: int
    succeeded: int
    skipped: int
    failed: int


class JobLockData(BaseModel):
    """Current global write lock metadata."""

    acquired: bool
    owner: str | None
    expires_at: datetime | None


class JobScopeDisplayData(BaseModel):
    """面向界面的任务范围名称。"""

    mode: str
    primary: str
    secondary: str | None


class JobData(BaseModel):
    """Task state returned by list and detail endpoints."""

    job_id: UUID
    batch_id: UUID | None
    base_batch_id: UUID | None
    status: str
    outcome: str | None
    scope_count: int
    scope_definition: dict[str, Any]
    model_policy: dict[str, Any] = Field(default_factory=dict)
    scope_display: JobScopeDisplayData
    requested_stages: list[str]
    scope_hash: str
    current_stage: str | None
    cancellation_requested: bool
    progress: JobProgress
    steps: list[JobStepData]
    lock: JobLockData
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_message: str | None
    manifest_path: str | None
    task_target: TaskTarget = TaskTarget.FUND_MONTHLY


class SupplementarySourceData(BaseModel):
    """单基金补充来源任务的创建结果。"""

    job: JobData
    estimated_minutes: int
    source_kind: str


class SecondaryFilterOption(BaseModel):
    """二级类别维度及可选标签。"""

    key: str
    label: str
    options: list[str]


class FundCollectionData(BaseModel):
    """临时基金集合。"""

    collection_id: UUID
    label: str
    fund_codes: list[str]
    count: int
    expires_at: datetime


class SourceManagementItem(BaseModel):
    """来源管理列表项。"""

    source_id: str
    label: str
    report_kind: str | None = None
    report_period: str | None = None
    is_visible: bool
    is_active: bool
    document_version_id: UUID | None = None
    metric_count: int = 0
    holding_count: int = 0


class SourceManagementData(BaseModel):
    """基金全部来源管理数据。"""

    fund_code: str
    items: list[SourceManagementItem] = Field(default_factory=list)


class JobPageData(BaseModel):
    """Paginated task list."""

    items: list[JobData]
    page: int
    page_size: int
    total: int


class JobFundScopeItemData(BaseModel):
    """任务内单基金的执行结果与原因。"""

    fund_id: UUID
    fund_code: str
    fund_name: str
    result_status: str
    skip_reason: str | None


class JobFundScopePageData(BaseModel):
    """任务基金结果分页。"""

    items: list[JobFundScopeItemData]
    page: int
    page_size: int
    total: int


class JobFundIssueData(BaseModel):
    """任务内按阶段聚合的跳过或失败基金。"""

    fund_id: UUID
    fund_code: str
    fund_name: str
    stage: str
    result_status: str
    reason: str


class JobFundIssuePageData(BaseModel):
    """任务异常基金明细。"""

    items: list[JobFundIssueData]
    total: int
    skipped: int
    failed: int


class JobEventData(BaseModel):
    """Replayable task event."""

    schema_version: str = "1.0"
    event_id: UUID
    job_id: UUID
    sequence_no: int
    type: str
    occurred_at: datetime
    snapshot_id: UUID | None
    payload: dict[str, Any]


class JobEventPageData(BaseModel):
    """Ordered task event list for polling clients and tests."""

    items: list[JobEventData]
    last_sequence_no: int


class PublishRequest(BaseModel):
    """Worker request to publish a validated staging manifest."""

    manifest_path: str
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    lease_token: str = Field(min_length=32, max_length=64)


class PublishResultData(BaseModel):
    """API publication result."""

    job_id: UUID
    batch_id: UUID
    status: str
    manifest_path: str
    manifest_sha256: str
    current_snapshot_id: UUID

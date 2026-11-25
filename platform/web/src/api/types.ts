export type QualityStatus =
  | "complete"
  | "missing_file"
  | "missing_metric"
  | "parse_failed"
  | "extraction_failed"
  | "validation_warning"
  | "review_required"
  | "stale";

export interface ApiEnvelope<T> {
  data: T;
  snapshot_id: string | null;
  request_id: string;
  meta: { server_time: string };
}

export interface ApiErrorEnvelope {
  error: {
    code: string;
    message: string;
    retryable: boolean;
    details: Record<string, unknown>;
  };
  request_id: string;
}

export interface Snapshot {
  snapshot_id: string;
  status: string;
  source_kind: string | null;
  source_sha256: string | null;
  coverage: number | null;
  fund_count: number;
  published_at: string | null;
  release_version: string | null;
}

export type ChatStatus = "answered" | "insufficient_evidence" | "evidence_only";
export type ChatRoute = "sql" | "sql+knowledge" | "knowledge_search";

export interface ChatRequest {
  question: string;
  fund_codes?: string[];
  report_period?: string;
  snapshot_id?: string;
  mode?: "fulltext" | "vector" | "hybrid";
  query_vector?: number[];
  limit?: number;
}

export interface ChatEvidence {
  evidence_id: string;
  evidence_type: "structured_record" | "document_chunk";
  supports: string;
  fund_code: string;
  fund_name: string;
  report_period: string | null;
  document_version_id: string | null;
  page_number: number | null;
  section_title: string | null;
  citation_tier: string | null;
  source_label: string | null;
  source_path: string | null;
  source_url: string | null;
  content: string;
  content_hash: string | null;
}

export interface ChatStructuredValue {
  fund_code: string;
  fund_name: string;
  report_period: string;
  field: string;
  value: number | string | null;
  unit: string | null;
  review_status: string | null;
  quality_status: string;
  document_version_id: string;
}

export interface ChatClaim {
  claim_id: string;
  text: string;
  evidence_ids: string[];
}

export interface ChatResponseData {
  question: string;
  status: ChatStatus;
  route: ChatRoute;
  answer: string;
  snapshot_id: string;
  structured_values: ChatStructuredValue[];
  claims: ChatClaim[];
  evidence: ChatEvidence[];
  limitations: string[];
  metadata: Record<string, unknown>;
}

export type AgentIntent =
  | "query"
  | "compare"
  | "supplementary_source"
  | "create_monthly_job"
  | "create_semiannual_job";
export type AgentRunStatus =
  | "read_only"
  | "needs_clarification"
  | "awaiting_confirmation"
  | "confirmed"
  | "rejected"
  | "failed";

export interface AgentPlanRequest {
  question: string;
  fund_codes?: string[];
  collection_id?: string;
  collection_session_key?: string;
  task_target?: "fund_monthly" | "fund_semiannual";
  structured_provider?: "deepseek" | "qwen" | "local";
}

export interface AgentPlanData {
  run_id: string;
  snapshot_id: string;
  status: AgentRunStatus;
  intent: AgentIntent;
  intent_envelope: Record<string, unknown> | null;
  question: string;
  plan: Record<string, unknown>;
  scope_definition: Record<string, unknown>;
  scope_count: number;
  task_target: string | null;
  model_policy: Record<string, unknown>;
  estimated_minutes: { minimum: number; maximum: number };
  impact_summary: string[];
  limitations: string[];
  requires_confirmation: boolean;
  confirmation_expires_at: string | null;
  job_id: string | null;
}

export interface ModelSettings {
  mineru: {
    mode: "lightweight" | "token";
    api_url: string;
    api_token: string;
    token_configured: boolean;
    token_hint: string;
  };
  structured_provider: "deepseek" | "qwen" | "local";
  structured_thinking: boolean;
  deepseek: {
    base_url: string;
    api_key: string;
    api_key_configured: boolean;
    api_key_hint: string;
    primary_model: string;
    fallback_model: string;
    vision_model: string;
  };
  qwen: {
    base_url: string;
    api_key: string;
    api_key_configured: boolean;
    api_key_hint: string;
    primary_model: string;
    fallback_model: string;
    vision_model: string;
  };
  local: {
    base_url: string;
    primary_model: string;
    workers: number;
  };
}

export interface LocalModelHealth {
  available: boolean;
  base_url: string;
  models: string[];
  reason: string | null;
}

export interface ModelSettingsUpdate {
  mineru: {
    mode: "lightweight" | "token";
    api_url: string;
    api_token?: string;
    clear_api_token: boolean;
  };
  structured_provider: "deepseek" | "qwen" | "local";
  structured_thinking: boolean;
  deepseek: {
    base_url: string;
    api_key?: string;
    clear_api_key: boolean;
    primary_model: string;
    fallback_model: string;
    vision_model: string;
  };
  qwen: {
    base_url: string;
    api_key?: string;
    clear_api_key: boolean;
    primary_model: string;
    fallback_model: string;
    vision_model: string;
  };
  local: {
    base_url: string;
    primary_model: string;
    workers: number;
  };
}

export interface FundSummary {
  fund_id: string;
  fund_code: string;
  name_cn: string;
  name_en: string | null;
  manager_id: string;
  manager_name: string;
  umbrella_name: string | null;
  asset_category: string | null;
  asset_subcategory: string | null;
  status: string;
  report_period: string | null;
  quality_status: QualityStatus;
  latest_scale?: number | null;
  latest_scale_date?: string | null;
  secondary_values?: Record<string, string | null>;
}

export interface FundPage {
  items: FundSummary[];
  page: number;
  page_size: number;
  total: number;
  matching_fund_codes?: string[];
  secondary_options?: Array<{ key: string; label: string; options: string[] }>;
}

export interface ReportSource {
  source_id: string;
  source_sheet: string;
  source_row: number;
  monthly_report_url: string | null;
  fallback_report_url: string | null;
  run_command: string | null;
  match_status: string;
  match_method: string | null;
  match_confidence: string | null;
  is_active: boolean;
  is_visible?: boolean;
  priority?: number;
  semiannual_report_url?: string | null;
  semiannual_run_command?: string | null;
  annual_report_url?: string | null;
  annual_run_command?: string | null;
  source_label: string | null;
  report_kind: string | null;
  report_period: string | null;
  document_version_id: string | null;
  language: string | null;
  available_sections: string[];
  download_candidates: Array<{
    rank: number;
    kind: string;
    stability: string;
    source_url: string;
    run_command: string;
    reason: string;
  }>;
}

export interface MetricValue {
  metric_name: string;
  numeric_value: number | null;
  text_value: string | null;
  unit: string | null;
  report_period: string;
  review_status: string;
  quality_status: QualityStatus;
  source_page: number | null;
  document_version_id: string;
  source_label: string | null;
}

export interface Holding {
  security_name: string;
  ticker: string | null;
  weight: number | null;
  market_value: number | null;
  holding_quantity: number | null;
  registration_place: string | null;
  holding_type: string | null;
  report_period: string;
  source_page: number | null;
  source_label: string | null;
}

export interface Allocation {
  dimension: string;
  category: string;
  weight: number | null;
  unit: string | null;
  report_period: string;
  source_page: number | null;
  source_label: string | null;
}

export interface FundReportData {
  report_period: string;
  document_version_id: string;
  source_label: string | null;
  report_kind: string | null;
  metrics: MetricValue[];
  holdings: Holding[];
  allocations: Allocation[];
}

export interface FundDetail {
  fund_id: string;
  fund_code: string;
  name_cn: string;
  name_en: string | null;
  manager_id: string;
  manager_name: string;
  manager_name_en: string | null;
  manager_short_name: string | null;
  umbrella_name: string | null;
  umbrella_code: string | null;
  asset_category: string | null;
  asset_subcategory: string | null;
  domicile: string | null;
  base_currency: string | null;
  management_style: string | null;
  status: string;
  sfc_authorized_at: string | null;
  regulation_category: string | null;
  tracking_index: string | null;
  quality_status: QualityStatus;
  report_period: string | null;
  metrics: MetricValue[];
  holdings: Holding[];
  allocations: Allocation[];
  reports: FundReportData[];
  report_sources: ReportSource[];
  regions: Array<{ level: string; name: string }>;
  tags: Array<{ type: string; value: string }>;
  stock_codes: string[];
  latest_document: {
    document_version_id: string;
    report_date: string | null;
    source_url: string | null;
    file_path: string | null;
    sha256: string;
  } | null;
}

export interface QualityIssue {
  issue_id: string;
  fund_id: string | null;
  fund_code: string | null;
  fund_name: string | null;
  severity: string;
  issue_type: string;
  details: string;
  status: string;
  created_at: string;
  metadata: Record<string, unknown>;
  manager_name?: string | null;
  asset_category?: string | null;
  asset_subcategory?: string | null;
  base_currency?: string | null;
  domicile?: string | null;
  fund_status?: string | null;
  report_period?: string | null;
}

export interface QualityPage {
  items: QualityIssue[];
  page: number;
  page_size: number;
  total: number;
}

export interface QualityReview {
  issue: QualityIssue;
  fund: FundDetail | null;
  record: Record<string, unknown>;
  audit: Record<string, unknown>;
}

export interface QualityReviewAction {
  issue_id: string;
  status: string;
  updated_at: string;
  changed_sections: string[];
}

export interface PerspectiveView {
  view_id: "fund_scale_histogram" | "fund_scale_trend" | "portfolio_structure";
  title: string;
  chart_type: "histogram" | "line" | "pie";
  dimension: string;
  metric: string;
  unit: string;
  dataset: Array<Record<string, unknown>>;
  source_period: string | null;
  snapshot_id: string;
  quality: QualityStatus;
}

export interface PerspectiveData {
  views: PerspectiveView[];
  scope_count: number;
}

export type JobStatus =
  | "waiting_lock"
  | "queued"
  | "running"
  | "retrying"
  | "validating"
  | "review_required"
  | "publishing"
  | "completed"
  | "failed"
  | "cancelled"
  | "publish_failed";

export interface JobProgress {
  total: number;
  processed: number;
  succeeded: number;
  skipped: number;
  failed: number;
}

export interface JobStep {
  step_type: string;
  status: string;
  total: number;
  succeeded: number;
  skipped: number;
  failed: number;
}

export interface Job {
  job_id: string;
  batch_id: string | null;
  base_batch_id: string | null;
  status: JobStatus;
  outcome: string | null;
  scope_count: number;
  scope_definition: Record<string, unknown>;
  model_policy?: {
    structured_provider?: "deepseek" | "qwen" | "local";
    local_model?: string;
    local_workers?: number;
  };
  scope_display: {
    mode: string;
    primary: string;
    secondary: string | null;
  };
  requested_stages: string[];
  scope_hash: string;
  current_stage: string | null;
  cancellation_requested: boolean;
  progress: JobProgress;
  steps: JobStep[];
  lock: {
    acquired: boolean;
    owner: string | null;
    expires_at: string | null;
  };
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error_code: string | null;
  error_message: string | null;
  manifest_path: string | null;
  task_target?: "fund_monthly" | "fund_semiannual" | "master_database";
}

export interface JobPage {
  items: Job[];
  page: number;
  page_size: number;
  total: number;
}

export interface MasterDatabaseOptions {
  default_reference_file: string;
  reference_files: string[];
  default_sheet: string;
  default_rename_sheet: string;
  crawler_root: string | null;
  crawler_available: boolean;
}

export interface MasterDatabaseTaskConfig {
  reference_file: string;
  sheet_name: string;
  rename_sheet_name: string;
  use_cache: boolean;
  rename_detection_mode: string;
  update_data_mode: string;
  auto_import: boolean;
}

export type JobScopeMode = "all" | "manager" | "category" | "funds" | "collection";

export interface JobScopeRequest {
  mode: JobScopeMode;
  manager_ids?: string[];
  categories?: string[];
  fund_codes?: string[];
  tag_filters?: string[];
  collection_id?: string;
  collection_session_key?: string;
}

export interface FundCollection {
  collection_id: string;
  label: string;
  fund_codes: string[];
  count: number;
  expires_at: string;
}

export interface SourceManagementItem {
  source_id: string;
  label: string;
  report_kind: string | null;
  report_period: string | null;
  is_visible: boolean;
  is_active: boolean;
  document_version_id: string | null;
  metric_count: number;
  holding_count: number;
}

export interface SourceManagementData {
  fund_code: string;
  items: SourceManagementItem[];
}

export interface JobScopeItem {
  fund_id: string;
  fund_code: string;
  fund_name: string;
  result_status: string;
  skip_reason: string | null;
}

export interface JobScopePage {
  items: JobScopeItem[];
  page: number;
  page_size: number;
  total: number;
}

export interface JobFundIssue {
  fund_id: string;
  fund_code: string;
  fund_name: string;
  stage: string;
  result_status: "skipped" | "failed";
  reason: string;
}

export interface JobFundIssuePage {
  items: JobFundIssue[];
  total: number;
  skipped: number;
  failed: number;
}

export interface ScopeOptions {
  total_funds: number;
  managers: Array<{
    manager_id: string;
    manager_name: string;
    fund_count: number;
  }>;
  categories: Array<{
    category: string;
    fund_count: number;
  }>;
}

export interface JobEvent {
  schema_version: string;
  event_id: string;
  job_id: string;
  sequence_no: number;
  type: string;
  occurred_at: string;
  snapshot_id: string | null;
  payload: Record<string, unknown>;
}

export interface JobEventPage {
  items: JobEvent[];
  last_sequence_no: number;
}

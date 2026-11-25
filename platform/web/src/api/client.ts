import type {
  ApiEnvelope,
  ApiErrorEnvelope,
  AgentPlanData,
  AgentPlanRequest,
  ChatRequest,
  ChatResponseData,
  FundDetail,
  FundPage,
  Job,
  JobEventPage,
  JobFundIssuePage,
  JobPage,
  JobScopeRequest,
  JobScopePage,
  ModelSettings,
  ModelSettingsUpdate,
  LocalModelHealth,
  PerspectiveData,
  QualityPage,
  QualityReview,
  QualityReviewAction,
  ScopeOptions,
  Snapshot,
  FundCollection,
  SourceManagementData,
  MasterDatabaseOptions,
  MasterDatabaseTaskConfig,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  readonly code: string;
  readonly requestId: string;
  readonly retryable: boolean;
  readonly status: number;

  constructor(
    message: string,
    options: { code: string; requestId: string; retryable: boolean; status: number },
  ) {
    super(message);
    this.name = "ApiError";
    this.code = options.code;
    this.requestId = options.requestId;
    this.retryable = options.retryable;
    this.status = options.status;
  }
}

function queryString(values: Record<string, unknown>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      for (const item of value) params.append(key, String(item));
    } else {
      params.set(key, String(value));
    }
  }
  const value = params.toString();
  return value ? `?${value}` : "";
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<ApiEnvelope<T>> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      Accept: "application/json",
      ...(options.body && !(options.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  // Read the body once and parse it defensively. When the API process is down
  // (or a reverse proxy returns an empty HTML/502 body), calling
  // `response.json()` directly throws a raw SyntaxError and hides the actual
  // service failure from the user.
  const rawBody = await response.text();
  let payload: ApiEnvelope<T> | ApiErrorEnvelope;
  try {
    payload = JSON.parse(rawBody) as ApiEnvelope<T> | ApiErrorEnvelope;
  } catch {
    const requestId = response.headers.get("X-Request-Id") ?? "";
    const isEmpty = rawBody.trim().length === 0;
    throw new ApiError(
      isEmpty
        ? `服务返回空响应 (${response.status})，请确认 API 服务正在运行`
        : `服务返回了非 JSON 响应 (${response.status})`,
      {
        code: "E_INVALID_RESPONSE",
        requestId,
        retryable: response.status >= 500 || response.status === 0,
        status: response.status,
      },
    );
  }
  if (!payload || typeof payload !== "object") {
    throw new ApiError(`服务返回了无效 JSON 响应 (${response.status})`, {
      code: "E_INVALID_RESPONSE",
      requestId: response.headers.get("X-Request-Id") ?? "",
      retryable: response.status >= 500,
      status: response.status,
    });
  }
  if (!response.ok || "error" in payload) {
    const error = "error" in payload ? payload.error : undefined;
    throw new ApiError(error?.message ?? `请求失败 (${response.status})`, {
      code: error?.code ?? "E_NETWORK",
      requestId: payload.request_id ?? response.headers.get("X-Request-Id") ?? "",
      retryable: error?.retryable ?? response.status >= 500,
      status: response.status,
    });
  }
  return payload;
}

export interface FundFilters {
  q?: string;
  manager?: string;
  category?: string;
  reportPeriod?: string;
  status?: string;
  page?: number;
  pageSize?: number;
  secondary?: string[];
  sortBy?: string;
  sortDirection?: string;
}

export const api = {
  snapshot: () => request<Snapshot>("/api/snapshots/current"),

  chat: (body: ChatRequest) =>
    request<ChatResponseData>("/api/chat", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  agentPlan: (body: AgentPlanRequest) =>
    request<AgentPlanData>("/api/agent/plan", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  agentRun: (runId: string) =>
    request<AgentPlanData>(`/api/agent/runs/${encodeURIComponent(runId)}`),

  confirmAgentRun: (runId: string, confirmed: boolean) =>
    request<AgentPlanData>(`/api/agent/runs/${encodeURIComponent(runId)}/confirm`, {
      method: "POST",
      body: JSON.stringify({ confirmed }),
    }),

  modelSettings: () => request<ModelSettings>("/api/settings/models"),

  localModelHealth: () => request<LocalModelHealth>("/api/settings/models/local-health"),

  saveModelSettings: (body: ModelSettingsUpdate) =>
    request<ModelSettings>("/api/settings/models", {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  funds: (filters: FundFilters = {}) =>
    request<FundPage>(
      `/api/funds${queryString({
        q: filters.q,
        manager: filters.manager,
        category: filters.category,
        report_period: filters.reportPeriod,
        status: filters.status,
        page: filters.page ?? 1,
        page_size: filters.pageSize ?? 20,
        secondary: filters.secondary,
        sort_by: filters.sortBy,
        sort_direction: filters.sortDirection,
      })}`,
    ),

  fund: (code: string) =>
    request<FundDetail>(`/api/funds/${encodeURIComponent(code)}`),

  createFundCollection: (fundCodes: string[], sessionKey: string, label = "已选基金集合") =>
    request<FundCollection>("/api/funds/collections", {
      method: "POST",
      body: JSON.stringify({ fund_codes: fundCodes, session_key: sessionKey, label }),
    }),

  fundCollections: (sessionKey: string) =>
    request<FundCollection[]>(`/api/funds/collections${queryString({ session_key: sessionKey })}`),

  sourceManagement: (fundCode: string) =>
    request<SourceManagementData>(`/api/funds/${encodeURIComponent(fundCode)}/sources/manage`),

  sourceManagementAction: (fundCode: string, sourceIds: string[], action: "hide" | "show" | "delete") =>
    request<SourceManagementData>(`/api/funds/${encodeURIComponent(fundCode)}/sources/manage`, {
      method: "POST",
      body: JSON.stringify({ source_ids: sourceIds, action }),
    }),

  quality: (params: {
    severity?: string;
    status?: string;
    fundCode?: string;
    page?: number;
    pageSize?: number;
  } = {}) =>
    request<QualityPage>(
      `/api/quality/issues${queryString({
        severity: params.severity,
        status: params.status,
        fund_code: params.fundCode,
        page: params.page ?? 1,
        page_size: params.pageSize ?? 50,
      })}`,
    ),

  qualityReview: (issueId: string) =>
    request<QualityReview>(`/api/quality/issues/${encodeURIComponent(issueId)}/review`),

  confirmQuality: (issueId: string, comment?: string) =>
    request<QualityReviewAction>(`/api/quality/issues/${encodeURIComponent(issueId)}/confirm`, {
      method: "POST",
      body: JSON.stringify({ comment: comment ?? null }),
    }),

  saveQualityReview: (
    issueId: string,
    record: Record<string, unknown>,
    options: { comment?: string; resolve?: boolean } = {},
  ) =>
    request<QualityReviewAction>(`/api/quality/issues/${encodeURIComponent(issueId)}/review`, {
      method: "PUT",
      body: JSON.stringify({
        record,
        comment: options.comment ?? null,
        resolve: options.resolve ?? true,
      }),
    }),

  perspectives: (params: {
    fundCodes?: string[];
    managerId?: string;
    category?: string;
    period?: string;
    sourceDocumentVersionId?: string;
  } = {}) => {
    // 浏览器/反向代理通常会限制请求行大小。基金数据库的“全部基金”范围
    // 可能包含数千个代码，改用 JSON body，避免 431 Request Header Fields Too Large。
    const fundCodes = params.fundCodes ?? [];
    const query = {
      scope_fund_codes: fundCodes,
      manager_id: params.managerId,
      category: params.category,
      period: params.period ?? "latest",
      source_document_version_id: params.sourceDocumentVersionId,
    };
    if (fundCodes.length > 100) {
      return request<PerspectiveData>("/api/analytics/perspectives/query", {
        method: "POST",
        body: JSON.stringify(query),
      });
    }
    return request<PerspectiveData>(
      `/api/analytics/perspectives${queryString(query)}`,
    );
  },

  jobs: (params: { status?: string; page?: number; pageSize?: number } = {}) =>
    request<JobPage>(
      `/api/jobs${queryString({
        status: params.status,
        page: params.page ?? 1,
        page_size: params.pageSize ?? 20,
      })}`,
    ),

  job: (jobId: string) => request<Job>(`/api/jobs/${jobId}`),

  scopeOptions: () => request<ScopeOptions>("/api/jobs/scope-options"),

  masterDatabaseOptions: () => request<MasterDatabaseOptions>("/api/jobs/master-database/options"),

  createJob: (
    scope: JobScopeRequest,
    stages: string[],
    idempotencyKey: string,
    inputJobId?: string,
    modelPolicy?: { structured_provider: "deepseek" | "qwen" | "local"; local_model?: string; local_workers?: number },
    taskTarget: "fund_monthly" | "fund_semiannual" | "master_database" = "fund_monthly",
    masterConfig?: MasterDatabaseTaskConfig,
  ) =>
    request<Job>("/api/jobs", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({
        scope,
        stages,
        input_job_id: inputJobId ?? null,
        document_policy: taskTarget === "master_database"
          ? { master_database: masterConfig }
          : { prefer: taskTarget === "fund_semiannual" ? "semiannual" : "monthly" },
        model_policy: modelPolicy ?? {},
        task_target: taskTarget,
      }),
    }),

  createSupplementarySource: (
    fundCode: string,
    input: { url?: string; file?: File; provider: "deepseek" | "qwen" | "local"; fixedCandidate: boolean; idempotencyKey: string },
  ) => {
    const form = new FormData();
    if (input.url?.trim()) form.append("source_url", input.url.trim());
    if (input.file) form.append("source_file", input.file);
    form.append("structured_provider", input.provider);
    form.append("fixed_candidate", String(input.fixedCandidate));
    return request<{ job: Job; estimated_minutes: number; source_kind: string }>(
      `/api/funds/${encodeURIComponent(fundCode)}/supplementary-source`,
      {
        method: "POST",
        headers: { "Idempotency-Key": input.idempotencyKey },
        body: form,
      },
    );
  },

  cancelJob: (jobId: string) =>
    request<Job>(`/api/jobs/${jobId}/cancel`, {
      method: "POST",
      headers: { "Idempotency-Key": `cancel-${jobId}` },
    }),

  retryJob: (jobId: string) =>
    request<Job>(`/api/jobs/${jobId}/retry`, {
      method: "POST",
      headers: { "Idempotency-Key": `retry-${jobId}` },
    }),

  importMasterDatabase: (jobId: string) =>
    request<{ job_id: string; workbook_path: string; summary: Record<string, unknown> }>(
      `/api/jobs/${jobId}/master-import`,
      {
        method: "POST",
        headers: { "Idempotency-Key": `master-import-${jobId}` },
      },
    ),

  masterDatabaseArtifactUrl: (jobId: string) => `${API_BASE}/api/jobs/${jobId}/master-artifact`,

  jobFunds: (
    jobId: string,
    params: { resultStatuses?: string[]; page?: number; pageSize?: number } = {},
  ) =>
    request<JobScopePage>(
      `/api/jobs/${jobId}/funds${queryString({
        result_status: params.resultStatuses,
        page: params.page ?? 1,
        page_size: params.pageSize ?? 500,
      })}`,
    ),

  jobIssues: (jobId: string) =>
    request<JobFundIssuePage>(`/api/jobs/${jobId}/issues`),

  jobEventHistory: (jobId: string, limit = 200) =>
    request<JobEventPage>(
      `/api/jobs/${jobId}/events/history${queryString({ limit })}`,
    ),

  health: () =>
    request<{
      status: string;
      database: string;
      snapshot_id: string | null;
      fund_count: number;
      report_source_count: number;
    }>("/api/health"),
};

export function eventSource(jobId: string, afterSequence = 0): EventSource {
  return new EventSource(
    `${API_BASE}/api/jobs/${jobId}/events${queryString({
      after_sequence: afterSequence > 0 ? afterSequence : undefined,
    })}`,
  );
}

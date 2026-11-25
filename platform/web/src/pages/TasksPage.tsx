import { useEffect, useMemo, useRef, useState } from "react";
import {
  Ban,
  ChevronDown,
  ChevronRight,
  Play,
  Radio,
  RefreshCw,
  RotateCcw,
  Workflow,
} from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError, eventSource } from "../api/client";
import type {
  Job,
  JobEvent,
  JobFundIssue,
  JobFundIssuePage,
  JobPage,
  JobScopeMode,
  JobScopeRequest,
  FundCollection,
  MasterDatabaseOptions,
  MasterDatabaseTaskConfig,
  ScopeOptions,
} from "../api/types";
import { PageHeader } from "../components/PageHeader";
import { EmptyState, ErrorState, LoadingState } from "../components/States";
import { JobBadge } from "../components/StatusBadge";
import {
  eventSummary,
  eventStatusLabel,
  formatEventDateTime,
  formatDateTime,
  isTerminalJob,
  jobStatusLabel,
} from "../lib/format";
import { mergeJobEvent } from "../lib/jobEvents";
import { mergeJobIssueEvent } from "../lib/jobIssues";

const stages = [
  { value: "download", label: "下载" },
  { value: "parse", label: "解析" },
  { value: "structure", label: "结构化" },
];
const stageOrder = stages.map((stage) => stage.value);

export function orderedStages(values: string[]): string[] {
  const selected = new Set(values);
  return stageOrder.filter((stage) => selected.has(stage));
}

const stageLabels: Record<string, string> = Object.fromEntries(
  stages.map((stage) => [stage.value, stage.label]),
);

function stageLabel(value: string | null | undefined): string {
  const stage = String(value ?? "");
  return stageLabels[stage] ?? (stage || "未开始");
}

function masterDatabaseConfigFromJob(job: Job): Partial<MasterDatabaseTaskConfig> {
  const policy = job.scope_definition.document_policy;
  if (!policy || typeof policy !== "object") return {};
  const raw = (policy as { master_database?: unknown }).master_database;
  return raw && typeof raw === "object"
    ? raw as Partial<MasterDatabaseTaskConfig>
    : {};
}

function runStageLabel(job: Job): string {
  if (job.task_target === "master_database") {
    if (job.current_stage === "validating") return "综合资料库 · 校验中";
    if (job.current_stage === "publishing") return "综合资料库 · 发布中";
    return "综合资料库 · 下载更新";
  }
  return stageLabel(job.current_stage) || jobStatusLabel(job.status);
}

function structuredDurationEstimate(
  fundCount: number,
  provider: "deepseek" | "qwen" | "local",
  includesStructure: boolean,
): string {
  /** 估算结构化阶段区间；参数为基金数量、模型路线和是否执行结构化。 */

  if (!includesStructure || fundCount < 1) return "未选择结构化";
  if (provider === "local") {
    return `${Math.max(3, fundCount * 3)}–${Math.max(6, fundCount * 6)} 分钟`;
  }
  return `${Math.max(1, Math.ceil(fundCount / 8))}–${Math.max(2, Math.ceil(fundCount / 3))} 分钟`;
}

function mineruDurationEstimate(fundCount: number, includesParse: boolean): string {
  /** 估算 MinerU 解析区间；参数为基金数量和是否执行解析。 */

  if (!includesParse || fundCount < 1) return "复用已有缓存";
  return `${Math.max(2, Math.ceil(fundCount / 36) * 2)}–${Math.max(5, Math.ceil(fundCount / 18) * 5)} 分钟`;
}

function progressPercent(job: Job): number {
  if (job.progress.total > 0) return Math.round((job.progress.processed / job.progress.total) * 100);
  if (["validating", "publishing"].includes(job.status)) return 90;
  if (job.status === "completed") return 100;
  return 0;
}

function scopeLabel(job: Job): string {
  if (job.task_target === "master_database") return "综合资料库";
  const mode = String(job.scope_definition.mode ?? "");
  if (mode === "all") return "全量基金";
  if (mode === "manager") return "按管理人";
  if (mode === "category") return "按资产类别";
  if (mode === "funds") return "指定基金";
  if (mode === "tag") return "按标签";
  return "任务范围";
}

function displayedTaskCount(job: Job): number {
  return job.task_target === "master_database" && job.progress.total > 0
    ? job.progress.total
    : job.scope_count;
}

function taskTargetLabel(job: Job): string {
  if (job.task_target === "master_database") return "综合资料库";
  if (job.task_target === "fund_semiannual") return "半年报";
  return "基金月报";
}

function modelPolicyLabel(job: Job): string {
  const provider = job.model_policy?.structured_provider
    ?? (job.scope_definition.model_policy as { structured_provider?: string } | undefined)?.structured_provider;
  if (provider === "local") {
    const model = job.model_policy?.local_model
      ?? (job.scope_definition.model_policy as { local_model?: string } | undefined)?.local_model;
    return `本地 Qwen${model ? ` · ${model}` : ""}`;
  }
  if (provider === "qwen") return "Qwen / DashScope";
  if (provider === "deepseek") return "DeepSeek";
  return "模型按全局设置";
}

function modelPolicyFromJob(job: Job): { structured_provider: "deepseek" | "qwen" | "local"; local_model?: string; local_workers?: number } | undefined {
  const policy = job.model_policy ?? job.scope_definition.model_policy;
  if (!policy || typeof policy !== "object") return undefined;
  const provider = String((policy as { structured_provider?: string }).structured_provider ?? "");
  if (provider !== "deepseek" && provider !== "qwen" && provider !== "local") return undefined;
  const localModel = String((policy as { local_model?: string }).local_model ?? "").trim();
  const localWorkers = Number((policy as { local_workers?: number }).local_workers);
  const workers = Number.isInteger(localWorkers) && localWorkers >= 1 && localWorkers <= 2
    ? localWorkers
    : undefined;
  return provider === "local" && localModel
    ? { structured_provider: provider, local_model: localModel, ...(workers ? { local_workers: workers } : {}) }
    : { structured_provider: provider };
}

function scopeFromJob(job: Job): JobScopeRequest {
  const scope = job.scope_definition;
  return {
    mode: String(scope.mode) as JobScopeMode,
    manager_ids: Array.isArray(scope.manager_ids)
      ? scope.manager_ids.map(String)
      : [],
    categories: Array.isArray(scope.categories)
      ? scope.categories.map(String)
      : [],
    fund_codes: Array.isArray(scope.fund_codes)
      ? scope.fund_codes.map(String)
      : [],
    tag_filters: Array.isArray(scope.tag_filters)
      ? scope.tag_filters.map(String)
      : [],
    collection_id: scope.collection_id ? String(scope.collection_id) : undefined,
    collection_session_key: scope.collection_session_key ? String(scope.collection_session_key) : undefined,
  };
}

function hasCompletedStep(job: Job, stage: string): boolean {
  return job.steps.some(
    (step) => step.step_type === stage && step.status === "completed",
  );
}

export function jobCanProvideFormScope(
  job: Job,
  mode: JobScopeMode,
  managerId: string,
  category: string,
  fundCode: string,
): boolean {
  const scope = job.scope_definition;
  // 全量任务可以为任意后续子范围提供输入产物。
  if (scope.mode === "all") return true;
  // 其它范围只在能从定义本身证明“前序范围包含当前范围”时展示，
  // 实际创建时后端还会按冻结的基金 ID 集合做最终子集校验。
  if (scope.mode !== mode) return false;
  if (mode === "manager") {
    return Array.isArray(scope.manager_ids)
      && scope.manager_ids.map(String).includes(managerId);
  }
  if (mode === "category") {
    return Array.isArray(scope.categories)
      && scope.categories.map(String).includes(category);
  }
  if (mode === "funds") {
    return Array.isArray(scope.fund_codes)
      && scope.fund_codes.map(String).includes(fundCode.trim());
  }
  return true;
}

export function taskDefaultsFromJob(job: Job | null) {
  if (!job) {
    return {
      scopeMode: "all" as JobScopeMode,
      managerId: "",
      category: "",
      fundCode: "",
      stages: ["download"],
    };
  }
  const scope = job.scope_definition;
  const rawMode = String(scope.mode ?? "all");
  const scopeMode: JobScopeMode = ["all", "manager", "category", "funds", "collection"].includes(rawMode)
    ? rawMode as JobScopeMode
    : "all";
  const validStages = job.requested_stages.filter((stage) =>
    ["download", "parse", "structure"].includes(stage),
  );
  return {
    scopeMode,
    managerId: Array.isArray(scope.manager_ids)
      ? String(scope.manager_ids[0] ?? "")
      : "",
    category: Array.isArray(scope.categories)
      ? String(scope.categories[0] ?? "")
      : "",
    fundCode: Array.isArray(scope.fund_codes)
      ? String(scope.fund_codes[0] ?? "")
      : "",
    stages: validStages.length ? orderedStages(validStages) : ["download"],
  };
}

export function TasksPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState<JobPage | null>(null);
  const [selected, setSelected] = useState<Job | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [fundCode, setFundCode] = useState("");
  const [taskTarget, setTaskTarget] = useState<"fund_monthly" | "fund_semiannual" | "master_database">("fund_monthly");
  const [scopeMode, setScopeMode] = useState<JobScopeMode>("all");
  const [managerId, setManagerId] = useState("");
  const [category, setCategory] = useState("");
  const [scopeOptions, setScopeOptions] = useState<ScopeOptions | null>(null);
  const [collectionOptions, setCollectionOptions] = useState<FundCollection[]>([]);
  const [masterOptions, setMasterOptions] = useState<MasterDatabaseOptions | null>(null);
  const [masterConfig, setMasterConfig] = useState<MasterDatabaseTaskConfig>({
    reference_file: "",
    sheet_name: "主数据",
    rename_sheet_name: "改名",
    use_cache: true,
    rename_detection_mode: "不检测",
    update_data_mode: "API（仅更新跨境理财通标签+最新规模）",
    auto_import: false,
  });
  const [inputJobId, setInputJobId] = useState("");
  const [issueData, setIssueData] = useState<JobFundIssuePage | null>(null);
  const [issueLoading, setIssueLoading] = useState(false);
  const [issueTick, setIssueTick] = useState(0);
  const [selectedStages, setSelectedStages] = useState(["download"]);
  const [structuredProvider, setStructuredProvider] = useState<"deepseek" | "qwen" | "local">("deepseek");
  const [submitting, setSubmitting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [masterImporting, setMasterImporting] = useState(false);
  const [tick, setTick] = useState(0);
  const [taskPanelHeight, setTaskPanelHeight] = useState<number | null>(null);
  const eventConsoleRef = useRef<HTMLDivElement>(null);
  const createPanelRef = useRef<HTMLElement>(null);
  const defaultsInitializedRef = useRef(false);

  useEffect(() => {
    const collectionId = searchParams.get("collection_id");
    if (collectionId) {
      setScopeMode("collection");
      setFundCode(collectionId);
    }
  }, [searchParams]);

  useEffect(() => {
    const sessionKey = window.sessionStorage.getItem("fund-collection-session");
    if (!sessionKey) {
      setCollectionOptions([]);
      if (searchParams.get("collection_id")) {
        setFundCode("");
        const next = new URLSearchParams(searchParams);
        next.delete("collection_id");
        setSearchParams(next, { replace: true });
      }
      return;
    }
    api
      .fundCollections(sessionKey)
      .then((response) => {
        setCollectionOptions(response.data);
        const requestedId = searchParams.get("collection_id");
        if (requestedId && !response.data.some((collection) => collection.collection_id === requestedId)) {
          setFundCode("");
          const next = new URLSearchParams(searchParams);
          next.delete("collection_id");
          setSearchParams(next, { replace: true });
        }
      })
      .catch(() => setCollectionOptions([]));
  }, [searchParams, setSearchParams, tick]);

  function loadJobs() {
    setLoading(true);
    api
      .jobs({ pageSize: 50 })
      .then((response) => {
        setData(response.data);
        setError(null);
        if (!defaultsInitializedRef.current) {
          const collectionId = searchParams.get("collection_id");
          if (collectionId) {
            setScopeMode("collection");
            setFundCode(collectionId);
          } else {
            const defaults = taskDefaultsFromJob(response.data.items[0] ?? null);
            setScopeMode(defaults.scopeMode);
            setManagerId(defaults.managerId);
            setCategory(defaults.category);
            setFundCode(defaults.fundCode);
            setSelectedStages(defaults.stages);
          }
          defaultsInitializedRef.current = true;
        }
        if (!selected && response.data.items.length) setSelected(response.data.items[0]);
      })
      .catch((reason: unknown) => setError(reason instanceof ApiError ? reason : null))
      .finally(() => setLoading(false));
  }

  useEffect(loadJobs, [searchParams, tick]);

  useEffect(() => {
    api
      .scopeOptions()
      .then((response) => {
        setScopeOptions(response.data);
        setManagerId((current) => current || response.data.managers[0]?.manager_id || "");
        setCategory((current) => current || response.data.categories[0]?.category || "");
      })
      .catch((reason: unknown) =>
        setError(reason instanceof ApiError ? reason : null),
      );
  }, []);

  useEffect(() => {
    api.masterDatabaseOptions()
      .then((response) => {
        setMasterOptions(response.data);
        setMasterConfig((current) => ({
          ...current,
          reference_file: current.reference_file || response.data.default_reference_file,
          sheet_name: current.sheet_name || response.data.default_sheet,
          rename_sheet_name: current.rename_sheet_name || response.data.default_rename_sheet,
        }));
      })
      .catch(() => setMasterOptions(null));
  }, []);

  useEffect(() => {
    if (taskTarget === "master_database") {
      setSelectedStages(["download"]);
      setScopeMode("all");
      setInputJobId("");
    }
  }, [taskTarget]);

  useEffect(() => {
    const createPanel = createPanelRef.current;
    if (!createPanel) return;
    const compactLayout = window.matchMedia("(max-width: 900px)");
    const syncHeight = () => {
      setTaskPanelHeight(
        compactLayout.matches
          ? null
          : createPanel.getBoundingClientRect().height,
      );
    };
    const observer = new ResizeObserver(syncHeight);
    observer.observe(createPanel);
    compactLayout.addEventListener("change", syncHeight);
    syncHeight();
    return () => {
      observer.disconnect();
      compactLayout.removeEventListener("change", syncHeight);
    };
  }, []);

  useEffect(() => {
    if (!selected) return;
    let active = true;
    let source: EventSource | null = null;

    const attach = (afterSequence: number) => {
      if (!active) return;
      source = eventSource(selected.job_id, afterSequence);
      const update = (event: MessageEvent) => {
        try {
          const next = JSON.parse(event.data) as JobEvent;
          if (!active) return;
          setEvents((current) =>
            [...current.filter((item) => item.event_id !== next.event_id), next]
              .sort((left, right) => left.sequence_no - right.sequence_no)
              .slice(-200),
          );
          if (["job.fund", "job.stage", "job.completed"].includes(next.type)) {
            setIssueTick((value) => value + 1);
          }
          setIssueData((current) => mergeJobIssueEvent(current, next));
          setSelected((current) =>
            current ? mergeJobEvent(current, next) : current,
          );
          setData((current) =>
            current
              ? {
                  ...current,
                  items: current.items.map((item) =>
                    item.job_id === next.job_id
                      ? mergeJobEvent(item, next)
                      : item,
                  ),
                }
              : current,
          );
          if (["job.completed", "job.warning"].includes(next.type)) {
            api.job(selected.job_id).then((response) => {
              if (!active) return;
              setSelected(response.data);
              setData((current) =>
                current
                  ? { ...current, items: current.items.map((item) => item.job_id === response.data.job_id ? response.data : item) }
                  : current,
              );
              if (next.type === "job.completed") {
                window.dispatchEvent(new Event("snapshot:refresh"));
              }
            }).catch(() => undefined);
            source?.close();
          }
        } catch {
          // SSE 的 heartbeat 不是 JSON 事件。
        }
      };
      ["job.snapshot", "job.stage", "job.fund", "job.backfill", "job.warning", "job.retry", "job.resume", "job.publish", "job.completed"].forEach((type) =>
        source?.addEventListener(type, update as EventListener),
      );
    };

    // 只回放最近 200 条，再从最后序号接收新增事件，避免每次挂载
    // 都把数千条历史事件从头推送并触发逐条滚动/刷新。
    api.jobEventHistory(selected.job_id)
      .then((response) => {
        if (!active) return;
        setEvents(response.data.items);
        const lastEvent = response.data.items.at(-1);
        if (
          isTerminalJob(selected.status)
          || lastEvent && ["job.completed", "job.warning"].includes(lastEvent.type)
        ) {
          return;
        }
        attach(response.data.last_sequence_no);
      })
      .catch(() => attach(0));
    return () => {
      active = false;
      source?.close();
    };
  }, [selected?.job_id]);
  useEffect(() => {
    if (!selected) {
      setIssueData(null);
      return;
    }
    let active = true;
    setIssueLoading(true);
    api
      .jobIssues(selected.job_id)
      .then((response) => {
        if (active) setIssueData(response.data);
      })
      .catch(() => {
        if (active) setIssueData(null);
      })
      .finally(() => {
        if (active) setIssueLoading(false);
      });
    return () => {
      active = false;
    };
  }, [
    selected?.job_id,
    selected?.status,
    selected?.progress.failed,
    selected?.progress.skipped,
    issueTick,
  ]);

  async function createTask(event: React.FormEvent) {
    event.preventDefault();
    if (!selectedStages.length) return;
    if (
      selectedStages.includes("download")
      && selectedStages.includes("structure")
      && !selectedStages.includes("parse")
    ) {
      return;
    }
    const needsInput =
      !selectedStages.includes("download")
      && selectedStages.some((stage) => stage === "parse" || stage === "structure");
    if (needsInput && !inputJobId) return;
    let scope: JobScopeRequest;
    if (taskTarget === "master_database") {
      scope = { mode: "all" };
    } else if (scopeMode === "all") {
      scope = { mode: "all" };
    } else if (scopeMode === "manager") {
      if (!managerId) return;
      scope = { mode: "manager", manager_ids: [managerId] };
    } else if (scopeMode === "category") {
      if (!category) return;
      scope = { mode: "category", categories: [category] };
    } else if (scopeMode === "collection") {
      const collectionId = searchParams.get("collection_id") || fundCode.trim();
      if (!collectionId) return;
      scope = {
        mode: "collection",
        collection_id: collectionId,
        collection_session_key: window.sessionStorage.getItem("fund-collection-session") ?? undefined,
      };
    } else {
      if (!fundCode.trim()) return;
      scope = { mode: "funds", fund_codes: [fundCode.trim()] };
    }
    setSubmitting(true);
    const key = `web-${Date.now()}-${crypto.randomUUID?.() ?? Math.random().toString(16).slice(2)}`;
    try {
      const response = await api.createJob(
        scope,
        orderedStages(selectedStages),
        key,
        needsInput ? inputJobId : undefined,
        selectedStages.includes("structure")
          ? { structured_provider: structuredProvider }
          : undefined,
        taskTarget,
        taskTarget === "master_database" ? masterConfig : undefined,
      );
      setSelected(response.data);
      setEvents([]);
      setTick((value) => value + 1);
      window.dispatchEvent(new Event("job:refresh"));
    } catch (reason) {
      setError(reason instanceof ApiError ? reason : null);
    } finally {
      setSubmitting(false);
    }
  }

  async function cancelSelected() {
    if (!selected) return;
    try {
      const response = await api.cancelJob(selected.job_id);
      setSelected(response.data);
      setTick((value) => value + 1);
      window.dispatchEvent(new Event("job:refresh"));
    } catch (reason) {
      setError(reason instanceof ApiError ? reason : null);
    }
  }

  async function retrySelected() {
    if (!selected) return;
    setRetrying(true);
    try {
      const response = await api.retryJob(selected.job_id);
      setSelected(response.data);
      setEvents([]);
      setIssueData(null);
      setTick((value) => value + 1);
      window.dispatchEvent(new Event("job:refresh"));
    } catch (reason) {
      setError(reason instanceof ApiError ? reason : null);
    } finally {
      setRetrying(false);
    }
  }

  async function importSelectedMaster() {
    if (!selected || selected.task_target !== "master_database" || selected.status !== "completed") return;
    setMasterImporting(true);
    try {
      await api.importMasterDatabase(selected.job_id);
      setTick((value) => value + 1);
      window.dispatchEvent(new Event("snapshot:refresh"));
    } catch (reason) {
      setError(reason instanceof ApiError ? reason : null);
    } finally {
      setMasterImporting(false);
    }
  }

  async function continueFromJob(job: Job, stage: "parse" | "structure") {
    setSubmitting(true);
    const key = `continue-${job.job_id}-${stage}-${Date.now()}`;
    try {
      const response = await api.createJob(
        scopeFromJob(job),
        [stage],
        key,
        job.job_id,
        stage === "structure" ? modelPolicyFromJob(job) : undefined,
        job.task_target ?? "fund_monthly",
      );
      setSelected(response.data);
      setEvents([]);
      setTick((value) => value + 1);
      window.dispatchEvent(new Event("job:refresh"));
    } catch (reason) {
      setError(reason instanceof ApiError ? reason : null);
    } finally {
      setSubmitting(false);
    }
  }

  const activeCount = useMemo(
    () => data?.items.filter((job) => !isTerminalJob(job.status)).length ?? 0,
    [data],
  );
  const visibleEvents = useMemo(() => {
    const visible: JobEvent[] = [];
    let previousRetry = "";
    for (const event of events) {
      if (event.type === "job.retry") {
        if (
          event.payload.phase !== "source_fallback" &&
          event.payload.phase !== "retry_wait"
          && Number(event.payload.attempt ?? 1) <= 1
        ) {
          continue;
        }
        const summary = eventSummary(event);
        if (summary === previousRetry) continue;
        previousRetry = summary;
      } else {
        previousRetry = "";
      }
      visible.push(event);
    }
    return visible;
  }, [events]);
  const lastEventKey = useMemo(() => {
    const event = visibleEvents.at(-1);
    return event
      ? `${event.event_id}:${event.sequence_no}:${JSON.stringify(event.payload)}`
      : "empty";
  }, [visibleEvents]);
  useEffect(() => {
    const consoleNode = eventConsoleRef.current;
    if (!consoleNode) return;
    const frame = window.requestAnimationFrame(() => {
      consoleNode.scrollTo({
        top: consoleNode.scrollHeight,
        behavior: "smooth",
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [lastEventKey, selected?.job_id]);

  const needsInput =
    !selectedStages.includes("download")
    && selectedStages.some((stage) => stage === "parse" || stage === "structure");
  const invalidStageCombination =
    selectedStages.includes("download")
    && selectedStages.includes("structure")
    && !selectedStages.includes("parse");
  const requiredSourceStage = selectedStages.includes("parse")
    ? "download"
    : "parse";
  const inputCandidates = useMemo(
    () =>
      (data?.items ?? []).filter(
        (job) =>
          isTerminalJob(job.status)
          && hasCompletedStep(job, requiredSourceStage)
          && jobCanProvideFormScope(
            job,
            scopeMode,
            managerId,
            category,
            fundCode,
          ),
      ),
    [category, data, fundCode, managerId, requiredSourceStage, scopeMode],
  );
  const inputCandidateIds = inputCandidates.map((job) => job.job_id).join(",");
  useEffect(() => {
    if (!needsInput) {
      setInputJobId("");
      return;
    }
    if (inputJobId && !inputCandidateIds.split(",").includes(inputJobId)) {
      setInputJobId("");
    }
  }, [inputCandidateIds, inputJobId, needsInput]);
  const estimatedScopeCount =
    scopeMode === "all"
      ? scopeOptions?.total_funds ?? 0
      : scopeMode === "manager"
        ? scopeOptions?.managers.find((item) => item.manager_id === managerId)
            ?.fund_count ?? 0
        : scopeMode === "category"
          ? scopeOptions?.categories.find((item) => item.category === category)
              ?.fund_count ?? 0
          : scopeMode === "collection"
            ? Number(window.sessionStorage.getItem("fund-collection-count") ?? 0)
          : fundCode.trim()
            ? 1
            : 0;
  const canSubmit =
    selectedStages.length > 0
    && (taskTarget === "master_database"
      ? Boolean(masterOptions?.crawler_available && masterConfig.reference_file.trim())
      : (scopeMode === "all"
        || (scopeMode === "manager" && Boolean(managerId))
        || (scopeMode === "category" && Boolean(category))
        || (scopeMode === "collection" && Boolean(searchParams.get("collection_id") || fundCode.trim()))
        || (scopeMode === "funds" && Boolean(fundCode.trim()))))
    && (!needsInput || Boolean(inputJobId))
    && (taskTarget !== "master_database" || (selectedStages.length === 1 && selectedStages[0] === "download"));
  const canSubmitSafe = canSubmit && !invalidStageCombination;

  return (
    <>
      <PageHeader
        eyebrow="Controlled pipeline"
        title="数据任务"
        description="冻结基金范围、申请全局写锁，并观察现有脚本适配器和发布阶段的实时状态。"
        actions={<span className="result-count"><b>{activeCount}</b> 个活动任务</span>}
      />
      {error ? <ErrorState message={error.message} requestId={error.requestId} onRetry={() => setTick((v) => v + 1)} /> : null}
      <div className="tasks-layout">
        <aside className="task-create panel" ref={createPanelRef}>
          <div className="panel-heading">
            <div><span className="eyebrow">New run</span><h2>创建任务</h2></div>
            <Workflow size={18} />
          </div>
          <form onSubmit={createTask} data-task-target={taskTarget}>
            <label>
              <span>任务目标</span>
              <select value={taskTarget} onChange={(event) => setTaskTarget(event.target.value as typeof taskTarget)}>
                <option value="fund_monthly">基金月报</option>
                <option value="fund_semiannual">半年报</option>
                <option value="master_database">综合资料库</option>
              </select>
            </label>
            {taskTarget === "master_database" ? (
              <div className="master-database-settings">
                <div className="master-settings-title">综合资料库设置</div>
                <label>
                  <span>参考文件</span>
                  <input
                    list="master-reference-files"
                    value={masterConfig.reference_file}
                    onChange={(event) => setMasterConfig((current) => ({ ...current, reference_file: event.target.value }))}
                    placeholder="HKEX基金综合资料库_完整_20261008.xlsx"
                  />
                  <datalist id="master-reference-files">
                    {(masterOptions?.reference_files ?? []).map((path) => <option value={path} key={path} />)}
                  </datalist>
                </label>
                <div className="master-settings-grid">
                  <label>
                    <span>数据 Sheet</span>
                    <input value={masterConfig.sheet_name} onChange={(event) => setMasterConfig((current) => ({ ...current, sheet_name: event.target.value }))} />
                  </label>
                  <label>
                    <span>改名 Sheet</span>
                    <input value={masterConfig.rename_sheet_name} onChange={(event) => setMasterConfig((current) => ({ ...current, rename_sheet_name: event.target.value }))} placeholder="可留空" />
                  </label>
                </div>
                <label className="check-row checked master-check-row">
                  <input type="checkbox" checked={masterConfig.use_cache} onChange={(event) => setMasterConfig((current) => ({ ...current, use_cache: event.target.checked }))} />
                  <span><strong>使用缓存</strong><small>24 小时内复用旧爬虫请求缓存</small></span>
                </label>
                <div className="master-settings-grid">
                  <label>
                    <span>改名检测</span>
                    <select value={masterConfig.rename_detection_mode} onChange={(event) => setMasterConfig((current) => ({ ...current, rename_detection_mode: event.target.value }))}>
                      <option>不检测</option>
                      <option>API（不稳定）</option>
                      <option>详情页（较慢）</option>
                    </select>
                  </label>
                  <label>
                    <span>数据更新</span>
                    <select value={masterConfig.update_data_mode} onChange={(event) => setMasterConfig((current) => ({ ...current, update_data_mode: event.target.value }))}>
                      <option>API（仅更新跨境理财通标签+最新规模）</option>
                      <option>详情页+API（较慢，更新全部基础信息）</option>
                    </select>
                  </label>
                </div>
                <label>
                  <span>完成后处理</span>
                  <select value={masterConfig.auto_import ? "import" : "download"} onChange={(event) => setMasterConfig((current) => ({ ...current, auto_import: event.target.value === "import" }))}>
                    <option value="download">仅下载并缓存，稍后确认落库</option>
                    <option value="import">下载后自动落库并更新数据库</option>
                  </select>
                </label>
                {!masterOptions?.crawler_available ? <p className="settings-hint warning">未找到 HKEX_Fund_Crawl_v4，请检查 HKEX_CRAWLER_ROOT 配置。</p> : null}
              </div>
            ) : null}
            <label>
              <span>任务范围</span>
              <select
                value={scopeMode}
                onChange={(event) => setScopeMode(event.target.value as JobScopeMode)}
              >
                <option value="all">全量基金</option>
                <option value="manager">按管理人</option>
                <option value="category">按资产类别</option>
                <option value="funds">指定基金代码</option>
                <option value="collection">指定基金集合</option>
              </select>
            </label>
            {scopeMode === "manager" ? (
              <label>
                <span>管理人</span>
                <select value={managerId} onChange={(event) => setManagerId(event.target.value)}>
                  {scopeOptions?.managers.map((manager) => (
                    <option key={manager.manager_id} value={manager.manager_id}>
                      {manager.manager_name}（{manager.fund_count}）
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            {scopeMode === "category" ? (
              <label>
                <span>资产类别</span>
                <select value={category} onChange={(event) => setCategory(event.target.value)}>
                  {(scopeOptions?.categories ?? []).map((item) => (
                    <option key={item.category} value={item.category}>
                      {item.category}（{item.fund_count}）
                    </option>
                  ))}
                  {!(scopeOptions?.categories ?? []).some((item) => item.category === "货币") ? (
                    <option value="货币">货币（待数据库快照导入）</option>
                  ) : null}
                </select>
              </label>
            ) : null}
            {scopeMode === "funds" ? (
              <label>
                <span>基金代码</span>
                <input value={fundCode} onChange={(event) => setFundCode(event.target.value.toUpperCase())} placeholder="ALL924" />
              </label>
            ) : null}
            {scopeMode === "collection" ? (
              <label>
                <span>基金集合ID</span>
                <select
                  value={fundCode}
                  onChange={(event) => {
                    const value = event.target.value;
                    setFundCode(value);
                    const next = new URLSearchParams(searchParams);
                    if (value) next.set("collection_id", value); else next.delete("collection_id");
                    setSearchParams(next, { replace: true });
                  }}
                >
                  <option value="">请选择基金集合</option>
                  {collectionOptions.map((collection) => (
                    <option key={collection.collection_id} value={collection.collection_id}>
                      {collection.label} · {collection.count} 只 · {collection.collection_id.slice(0, 8)}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            {needsInput ? (
              <label>
                <span>输入任务</span>
                <select
                  value={inputJobId}
                  onChange={(event) => setInputJobId(event.target.value)}
                >
                  <option value="">请选择含{requiredSourceStage === "download" ? "下载" : "解析"}产物的任务</option>
                  {inputCandidates.map((job) => (
                    <option key={job.job_id} value={job.job_id}>
                      {job.scope_display.primary} · {job.scope_count} 只 · {job.job_id.slice(0, 8)}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <fieldset className="stage-options">
              <legend>执行阶段</legend>
              <div className="stage-option-grid">
                {stages.map((stage, index) => (
                  <label
                    className={`check-row ${selectedStages.includes(stage.value) ? "checked" : ""}`}
                    key={stage.value}
                  >
                    <input
                      type="checkbox"
                      checked={selectedStages.includes(stage.value)}
                      onChange={(event) =>
                        setSelectedStages((current) =>
                          event.target.checked
                            ? [...current, stage.value]
                            : current.filter((item) => item !== stage.value),
                        )
                      }
                    />
                    <span>
                      <small>0{index + 1}</small>
                      <strong>{stage.label}</strong>
                    </span>
                  </label>
                ))}
              </div>
              {selectedStages.includes("parse") ? (
                <p className="form-note">解析：MinerU（沿用“模型设置”中的轻量型 / Token 路线；若已有缓存会自动复用）。</p>
              ) : null}
              {selectedStages.includes("structure") ? (
                <div className="model-task-choice">
                  <strong>结构化模型</strong>
                  <div className="route-choice-grid" role="radiogroup" aria-label="本次结构化模型">
                    {(["local", "deepseek", "qwen"] as const).map((provider) => (
                      <label className={`route-choice ${structuredProvider === provider ? "selected" : ""}`} key={provider}>
                        <input type="radio" name="task-structured-provider" checked={structuredProvider === provider} onChange={() => setStructuredProvider(provider)} />
                        <span><strong>{provider === "local" ? "本地 Qwen 4B" : provider === "deepseek" ? "DeepSeek" : "Qwen / DashScope"}</strong><small>{provider === "local" ? "单并发 · 无云端模型费用 · 不含视觉复核" : "沿用模型设置中的 API 与模型"}</small></span>
                      </label>
                    ))}
                  </div>
                </div>
              ) : null}
            </fieldset>
            <p className="form-note">任务范围在提交时冻结；同一时间只允许一个写任务。</p>
            {invalidStageCombination ? (
              <p className="settings-hint warning">
                下载后直接结构化缺少解析产物，请同时勾选“解析”。
              </p>
            ) : null}
            <div className="scope-estimate">
              {taskTarget === "master_database" ? (
                <>综合资料库更新预计 <b>{masterConfig.update_data_mode.startsWith("详情页") ? "15–90 分钟" : "2–10 分钟"}</b><br />将生成 1 个 xlsx 缓存文件{masterConfig.auto_import ? "，完成后自动落库" : "，可在任务详情中确认落库"}</>
              ) : (
                <>本次预计冻结 <b>{estimatedScopeCount}</b> 只基金
                  {selectedStages.includes("parse") ? <><br />MinerU 解析预计 <b>{mineruDurationEstimate(estimatedScopeCount, true)}{taskTarget === "fund_semiannual" ? "（半年报含切片识别）" : ""}</b></> : null}
                  {selectedStages.includes("structure") ? <><br />结构化预计 <b>{structuredDurationEstimate(estimatedScopeCount, structuredProvider, true)}{taskTarget === "fund_semiannual" ? "（半年报持仓抽取）" : ""}</b></> : null}
                </>
              )}
            </div>
            <button className="button primary wide" disabled={submitting || !canSubmitSafe}>
              <Play size={15} /> {submitting ? "提交中…" : "创建并排队"}
            </button>
          </form>
        </aside>

        <section
          className="task-list panel"
          style={taskPanelHeight ? { height: `${taskPanelHeight}px` } : undefined}
        >
          <div className="panel-heading">
            <div><span className="eyebrow">Queue</span><h2>任务记录</h2></div>
            <button className="icon-button" onClick={() => setTick((v) => v + 1)}><RefreshCw size={16} /></button>
          </div>
          {loading ? <LoadingState label="正在同步任务…" /> : null}
          {!loading && !data?.items.length ? <EmptyState title="还没有任务" description="从左侧创建一个下载或解析任务。" /> : null}
          <div className="task-rows">
            {data?.items.map((job) => (
              <button
                key={job.job_id}
                className={`task-row ${selected?.job_id === job.job_id ? "selected" : ""}`}
                onClick={() => { setSelected(job); setEvents([]); }}
                aria-expanded={selected?.job_id === job.job_id}
                aria-controls={`task-detail-${job.job_id}`}
              >
                {selected?.job_id === job.job_id
                  ? <ChevronDown className="task-chevron" size={15} />
                  : <ChevronRight className="task-chevron" size={15} />}
                <span className="task-id">{job.job_id.slice(0, 8)}</span>
                <span className="task-scope">
                  <strong>{job.scope_display.primary}</strong>
                  <small>
                    {taskTargetLabel(job)}
                    {job.task_target === "master_database" ? "" : ` · ${job.scope_display.secondary ?? scopeLabel(job)}`}
                    {" · "}
                    {job.requested_stages.map(stageLabel).join(" → ")}
                    {job.requested_stages.includes("structure") ? ` · ${modelPolicyLabel(job)}` : ""}
                  </small>
                </span>
                <span className="task-time">
                  {displayedTaskCount(job)} 只 · {formatDateTime(job.created_at)}
                </span>
                <JobBadge status={job.status} />
              </button>
            ))}
          </div>
        </section>
      </div>

      {selected ? (
        <section
          className="task-detail panel"
          id={`task-detail-${selected.job_id}`}
        >
          <div className="task-detail-head">
            <div>
              <span className="eyebrow">Run detail</span>
              <h2>{selected.scope_display.primary}</h2>
              <code className="task-detail-id">{selected.job_id}</code>
              <small className="task-detail-model">任务目标：{taskTargetLabel(selected)}</small>
              {selected.requested_stages.includes("structure") ? (
                <small className="task-detail-model">结构化模型：{modelPolicyLabel(selected)}</small>
              ) : null}
            </div>
            <JobBadge status={selected.status} />
            {selected.task_target === "master_database" && selected.status === "completed" ? (
              <>
                <a className="button secondary" href={api.masterDatabaseArtifactUrl(selected.job_id)}>下载缓存 xlsx</a>
                <button className="button secondary" disabled={masterImporting} onClick={importSelectedMaster}>
                  {masterImporting ? "正在落库…" : "落库到数据库"}
                </button>
              </>
            ) : null}
            {hasCompletedStep(selected, "download")
            && !selected.requested_stages.includes("parse")
            && !hasCompletedStep(selected, "parse") ? (
              <button className="button secondary" disabled={submitting} onClick={() => continueFromJob(selected, "parse")}>
                单独执行解析
              </button>
            ) : null}
            {hasCompletedStep(selected, "parse")
            && !selected.requested_stages.includes("structure")
            && !hasCompletedStep(selected, "structure") ? (
              <button className="button secondary" disabled={submitting} onClick={() => continueFromJob(selected, "structure")}>
                单独执行结构化
              </button>
            ) : null}
            {!isTerminalJob(selected.status) ? (
              <button className="button danger" onClick={cancelSelected}><Ban size={15} /> 取消任务</button>
            ) : null}
            {["failed", "publish_failed"].includes(selected.status) ? (
              <button className="button secondary" disabled={retrying} onClick={retrySelected}>
                <RotateCcw size={15} /> {retrying ? "正在恢复…" : "断点续跑"}
              </button>
            ) : null}
          </div>
          <div className="task-detail-body">
            <div className="task-detail-main">
              <div className="run-progress">
                <div className="progress-label">
                  <span>{runStageLabel(selected)}</span>
                  <b>{progressPercent(selected)}%</b>
                </div>
                <div className="progress-track"><i style={{ width: `${progressPercent(selected)}%` }} /></div>
                <div className="run-counts">
                  <span>{scopeLabel(selected)} <b>{displayedTaskCount(selected)}</b></span>
                  {selected.task_target === "master_database" ? (
                    <span>已处理 <b>{selected.progress.processed}/{selected.progress.total || "—"}</b></span>
                  ) : (
                    <>
                      <span>成功 <b>{selected.progress.succeeded}</b></span>
                      <span>跳过 <b>{selected.progress.skipped}</b></span>
                      <span>失败 <b>{selected.progress.failed}</b></span>
                    </>
                  )}
                  {selected.lock.acquired ? <span>锁 <b>{selected.lock.owner}</b></span> : null}
                </div>
              </div>
              <div className="task-detail-stack">
                {selected.task_target === "master_database" ? (() => {
                  const config = masterDatabaseConfigFromJob(selected);
                  return (
                    <section className="task-detail-section master-run-card">
                      <div className="master-run-card-head">
                        <div>
                          <span className="eyebrow">Master database</span>
                          <h3>综合资料库更新</h3>
                        </div>
                        <span className="master-run-status">{jobStatusLabel(selected.status)}</span>
                      </div>
                      <div className="master-run-grid">
                        <div><small>数据更新方式</small><b>{String(config.update_data_mode ?? "未记录")}</b></div>
                        <div><small>参考工作簿</small><b>{String(config.reference_file ?? "未记录").split(/[\\/]/).at(-1)}</b></div>
                        <div><small>缓存策略</small><b>{config.use_cache === false ? "不使用缓存" : "使用缓存"}</b></div>
                        <div><small>自动落库</small><b>{config.auto_import ? "完成后自动落库" : "完成后手动确认"}</b></div>
                      </div>
                      <p className="master-run-note">
                        {selected.progress.total > 0
                          ? `已处理 ${selected.progress.processed}/${selected.progress.total} 只基金；进度来自旧爬虫实时输出。`
                          : "正在等待旧爬虫输出基金总数…"}
                      </p>
                    </section>
                  );
                })() : null}
                <section className="task-detail-section">
                  <h3>阶段</h3>
                  <div className="step-list">
                    {selected.task_target === "master_database" ? (
                      <div className={`step-item ${selected.status}`}>
                        <span>综合资料库更新</span>
                        <b>{jobStatusLabel(selected.status)}</b>
                        <small>
                          {selected.progress.processed}/{selected.progress.total || "—"} 只基金已处理
                        </small>
                      </div>
                    ) : selected.steps.map((step) => (
                      <div key={step.step_type} className={`step-item ${step.status}`}>
                        <span>{stageLabel(step.step_type)}</span>
                        <b>{step.status}</b>
                        <small>
                          {step.succeeded} 成功 · {step.skipped} 跳过 · {step.failed} 失败
                        </small>
                      </div>
                    ))}
                  </div>
                  {selected.error_message ? <div className="inline-error">{selected.error_code}: {selected.error_message}</div> : null}
                </section>
                <section className="task-detail-section event-section">
                  <h3><Radio size={15} /> 任务事件（可回放）</h3>
                  <div className="event-console" ref={eventConsoleRef}>
                    {visibleEvents.map((event) => (
                      <div key={event.event_id}>
                        <time>{formatEventDateTime(event.occurred_at)}</time>
                        <code>{event.type}</code>
                        <span>{eventSummary(event)}</span>
                      </div>
                    ))}
                    {!visibleEvents.length ? <p className="muted">选择任务后，阶段、补全和基金事件会在此回放。</p> : null}
                  </div>
                </section>
              </div>
            </div>
            <aside className="task-issues task-detail-aside">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">Task detail</span>
                  <h3>详情</h3>
                </div>
                <div className="issue-summary">
                  <span className="job-skipped">跳过 {issueData?.skipped ?? 0}</span>
                  <span className="job-failed">失败 {issueData?.failed ?? 0}</span>
                </div>
              </div>
              {issueLoading ? <LoadingState label="正在读取基金执行原因…" /> : null}
              {!issueLoading && !issueData?.items.length ? (
                <p className="muted">当前任务各阶段都没有跳过或失败的基金。</p>
              ) : null}
              <div className="scope-issue-list">
                {issueData?.items.map((item: JobFundIssue) => (
                  <article
                    className="scope-issue-row"
                    key={`${item.stage}-${item.fund_id}`}
                  >
                    <div>
                      <Link to={`/funds/${item.fund_code}`}>{item.fund_code}</Link>
                      <strong>{item.fund_name}</strong>
                    </div>
                    <span className="stage-badge">{stageLabel(item.stage)}</span>
                    <span className={`mini-status job-${item.result_status}`}>
                      {eventStatusLabel(item.result_status)}
                    </span>
                    <p><b>原因</b>{item.reason}</p>
                  </article>
                ))}
              </div>
            </aside>
          </div>
        </section>
      ) : null}
    </>
  );
}

import type { JobEvent, JobStatus, QualityStatus } from "../api/types";

export function formatDate(value: string | null | undefined): string {
  if (!value) return "未提供";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-HK", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-HK", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function formatEventDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-HK", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "未披露";
  return new Intl.NumberFormat("zh-HK", {
    maximumFractionDigits: digits,
    minimumFractionDigits: 0,
  }).format(value);
}

export const metricLabels: Record<string, string> = {
  inception_date: "成立日期",
  listing_date: "上市日期",
  fund_size_hkd_100m: "基金规模",
  fund_size_usd_million: "基金规模",
  portfolio_duration_years: "组合久期",
  portfolio_ytm_percent: "到期收益率",
  credit_rating: "信用评级",
  top_holdings_total_percent: "十大持仓合计",
  dividend_share_class: "派息份额",
  dividend_currency: "派息币种",
  dividend_frequency: "派息频率",
  dividend_per_unit: "每单位派息",
  annualized_dividend_yield_percent: "年度化派息率",
  annualized_return_percent: "年化收益率（总回报）",
  annualized_volatility_percent: "年化波动率",
  dividend_note: "派息说明",
  investment_objective: "投资目标",
  investment_strategy: "投资策略",
  portfolio_ytm_note: "到期收益率说明",
  seven_day_yield_percent: "七日年化收益率",
  annualized_yield_percent: "年化收益率",
  weighted_average_maturity_days: "加权平均到期期限",
  weighted_average_life_days: "加权平均存续期",
  daily_liquid_assets_percent: "每日流动资产",
  weekly_liquid_assets_percent: "每周流动资产",
  net_assets_usd: "净资产",
  total_assets_usd: "总资产",
  total_income_usd: "半年总收入",
  operating_profit_usd: "营运利润",
  distribution_paid_usd: "已分派金额",
  equity_weight_percent: "股票占分类资产",
  debt_weight_percent: "债券占分类资产",
  equity_debt_ratio: "股债比",
};

export const dimensionLabels: Record<string, string> = {
  geography: "地区配置",
  sector: "行业配置",
  duration: "久期分布",
  rating: "评级分布",
  asset_type: "资产类型",
  bank_exposure: "银行敞口",
};

export function qualityLabel(status: QualityStatus): string {
  return {
    complete: "数据完整",
    missing_file: "缺少文件",
    missing_metric: "缺少指标",
    parse_failed: "解析失败",
    extraction_failed: "抽取失败",
    validation_warning: "待复核",
    review_required: "需要复核",
    stale: "报告过期",
  }[status];
}

export function jobStatusLabel(status: JobStatus): string {
  return {
    waiting_lock: "等待任务锁",
    queued: "已排队",
    running: "运行中",
    retrying: "重试中",
    validating: "校验中",
    review_required: "待人工确认",
    publishing: "发布中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    publish_failed: "发布失败",
  }[status];
}

export function isTerminalJob(status: JobStatus): boolean {
  return ["completed", "failed", "cancelled", "publish_failed"].includes(status);
}

const eventStatusLabels: Record<string, string> = {
  waiting_lock: "WAITING FOR LOCK",
  queued: "QUEUED",
  running: "RUNNING",
  retrying: "RETRYING",
  validating: "VALIDATING",
  publishing: "PUBLISHING",
  completed: "COMPLETED",
  success: "SUCCEEDED",
  succeeded: "SUCCEEDED",
  skipped: "SKIPPED",
  failed: "FAILED",
  cancelled: "CANCELLED",
};

export function eventStatusLabel(value: unknown): string {
  const status = String(value ?? "").trim().toLowerCase();
  if (!status) return "UNKNOWN";
  return eventStatusLabels[status] ?? status.replaceAll("_", " ").toUpperCase();
}

function listCount(value: unknown): number {
  return Array.isArray(value) ? value.length : 0;
}

export function eventSummary(event: JobEvent): string {
  const payload = event.payload;
  if (event.type === "job.backfill") {
    if (payload.stage === "master_database") {
      const processed = Number(payload.processed);
      const total = Number(payload.total);
      const progress = Number.isFinite(processed) && Number.isFinite(total) && total > 0
        ? ` · ${Math.max(0, Math.min(processed, total))}/${total}`
        : "";
      return `综合资料库${progress} · ${String(payload.message ?? "正在运行")}`;
    }
    const sheet = String(payload.sheet ?? "Unknown");
    if (payload.status === "running") {
      const count = listCount(payload.rows);
      return `${eventStatusLabel(payload.status)} · ${sheet} · ${count} ${count === 1 ? "row" : "rows"}`;
    }
    return `${eventStatusLabel(payload.status)} · ${sheet} · SUCCEEDED ${listCount(payload.succeeded)} / SKIPPED ${listCount(payload.skipped)} / FAILED ${listCount(payload.failed)}`;
  }
  if (event.type === "job.retry") {
    if (payload.phase === "retry_wait") {
      return `RETRYING IN ${String(payload.retry_delay_seconds ?? "?")}S`;
    }
    if (payload.phase === "source_fallback") {
      return `SOURCE ${String(payload.candidate_rank ?? "?")} → ${String(payload.next_candidate_rank ?? "?")} · ${String(payload.reason ?? "FALLBACK")}`;
    }
    return `ATTEMPT ${String(payload.attempt ?? "?")}/${String(payload.max_attempts ?? "?")}`;
  }
  if (payload.stage) {
    const stage = String(payload.stage).toUpperCase();
    const status = eventStatusLabel(payload.status);
    const progress =
      payload.processed !== undefined && payload.total !== undefined
        ? ` · ${String(payload.processed)}/${String(payload.total)}`
        : "";
    return stage === status ? `${status}${progress}` : `${stage} · ${status}${progress}`;
  }
  return eventStatusLabel(payload.status);
}

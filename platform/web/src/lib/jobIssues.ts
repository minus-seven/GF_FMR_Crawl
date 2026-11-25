import type {
  JobEvent,
  JobFundIssue,
  JobFundIssuePage,
} from "../api/types";

const stageOrder: Record<string, number> = {
  download: 0,
  parse: 1,
  structure: 2,
};

function issuePage(items: JobFundIssue[]): JobFundIssuePage {
  return {
    items,
    total: items.length,
    skipped: items.filter((item) => item.result_status === "skipped").length,
    failed: items.filter((item) => item.result_status === "failed").length,
  };
}

export function mergeJobIssueEvent(
  current: JobFundIssuePage | null,
  event: JobEvent,
): JobFundIssuePage | null {
  if (event.type !== "job.fund") return current;
  const fundCode = String(event.payload.fund_code ?? "").trim();
  const status = String(event.payload.status ?? "").trim();
  if (!fundCode || !["success", "skipped", "failed", "cancelled", "running"].includes(status)) {
    return current;
  }
  const stage = String(event.payload.stage ?? "download");
  const key = `${stage}:${fundCode}`;
  const items = (current?.items ?? []).filter(
    (item) => `${item.stage}:${item.fund_code}` !== key,
  );
  if (status === "skipped" || status === "failed") {
    items.push({
      fund_id: String(event.payload.fund_id ?? `live:${fundCode}`),
      fund_code: fundCode,
      fund_name: String(event.payload.fund_name ?? fundCode),
      stage,
      result_status: status,
      reason: String(event.payload.reason ?? "").trim()
        || (status === "skipped" ? "任务已跳过" : "任务执行失败"),
    });
  }
  items.sort(
    (left, right) =>
      (stageOrder[left.stage] ?? 99) - (stageOrder[right.stage] ?? 99)
      || left.fund_code.localeCompare(right.fund_code),
  );
  return current || items.length ? issuePage(items) : null;
}

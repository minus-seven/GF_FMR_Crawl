import { describe, expect, it } from "vitest";
import type { JobEvent, JobFundIssuePage } from "../api/types";
import { mergeJobIssueEvent } from "../lib/jobIssues";

function event(status: string, code = "AAA001"): JobEvent {
  return {
    schema_version: "1.0",
    event_id: `event-${status}`,
    job_id: "job-1",
    sequence_no: 1,
    type: "job.fund",
    occurred_at: "2026-10-26T00:00:00Z",
    snapshot_id: null,
    payload: {
      stage: "download",
      fund_code: code,
      fund_name: "测试基金",
      status,
      reason: "官网未收录",
    },
  };
}

describe("live task issue projection", () => {
  it("adds skipped and failed funds as SSE events arrive", () => {
    const skipped = mergeJobIssueEvent(null, event("skipped"));
    const failed = mergeJobIssueEvent(skipped, event("failed", "BBB002"));

    expect(failed?.total).toBe(2);
    expect(failed?.skipped).toBe(1);
    expect(failed?.failed).toBe(1);
    expect(failed?.items[1].reason).toBe("官网未收录");
  });

  it("removes a live issue when a retry succeeds", () => {
    const withIssue: JobFundIssuePage | null = mergeJobIssueEvent(
      null,
      event("failed"),
    );
    const recovered = mergeJobIssueEvent(withIssue, event("success"));

    expect(recovered?.items).toHaveLength(0);
    expect(recovered?.failed).toBe(0);
  });
});

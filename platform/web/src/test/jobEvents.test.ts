import { describe, expect, it } from "vitest";
import type { Job, JobEvent } from "../api/types";
import { mergeJobEvent } from "../lib/jobEvents";

function job(): Job {
  return {
    job_id: "job-1",
    batch_id: null,
    base_batch_id: null,
    status: "running",
    outcome: null,
    scope_count: 45,
    scope_definition: { mode: "funds", fund_codes: ["ALL924"] },
    scope_display: {
      mode: "funds",
      primary: "测试基金",
      secondary: "ALL924",
    },
    requested_stages: ["parse"],
    scope_hash: "hash",
    current_stage: "parse",
    cancellation_requested: false,
    progress: {
      total: 45,
      processed: 0,
      succeeded: 0,
      skipped: 0,
      failed: 0,
    },
    steps: [],
    lock: { acquired: true, owner: "worker", expires_at: null },
    created_at: "2026-10-26T00:00:00Z",
    started_at: "2026-10-26T00:00:01Z",
    finished_at: null,
    error_code: null,
    error_message: null,
    manifest_path: null,
  };
}

function event(type: string, payload: Record<string, unknown>): JobEvent {
  return {
    schema_version: "1.0",
    event_id: "event-1",
    job_id: "job-1",
    sequence_no: 1,
    type,
    occurred_at: "2026-10-26T00:00:02Z",
    snapshot_id: null,
    payload,
  };
}

describe("job event projection", () => {
  it("updates parse progress from stage events", () => {
    const next = mergeJobEvent(
      job(),
      event("job.stage", {
        stage: "parse",
        status: "running",
        processed: 14,
        total: 45,
      }),
    );
    expect(next.current_stage).toBe("parse");
    expect(next.status).toBe("running");
    expect(next.progress.processed).toBe(14);
    expect(next.progress.total).toBe(45);
  });

  it("removes completed jobs from the active-task projection", () => {
    const next = mergeJobEvent(
      job(),
      event("job.completed", { status: "completed" }),
    );
    expect(next.status).toBe("completed");
    expect(next.current_stage).toBeNull();
    expect(next.progress.processed).toBe(45);
  });

  it("projects structured master-database backfill progress", () => {
    const next = mergeJobEvent(
      { ...job(), task_target: "master_database", scope_count: 2479 },
      event("job.backfill", {
        stage: "master_database",
        processed: 120,
        total: 2504,
        message: "成功获取中文详情页 [120/2504]",
      }),
    );
    expect(next.progress.processed).toBe(120);
    expect(next.progress.total).toBe(2504);
  });
});

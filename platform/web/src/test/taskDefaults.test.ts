import { describe, expect, it } from "vitest";
import type { Job } from "../api/types";
import {
  jobCanProvideFormScope,
  orderedStages,
  taskDefaultsFromJob,
} from "../pages/TasksPage";

function job(scope: Record<string, unknown>, stages = ["download"]): Job {
  return {
    job_id: "job-1",
    batch_id: null,
    base_batch_id: null,
    status: "completed",
    outcome: "complete",
    scope_count: 1,
    scope_definition: scope,
    scope_display: { mode: "funds", primary: "测试基金", secondary: "AAA001" },
    requested_stages: stages,
    scope_hash: "hash",
    current_stage: null,
    cancellation_requested: false,
    progress: { total: 1, processed: 1, succeeded: 1, skipped: 0, failed: 0 },
    steps: [],
    lock: { acquired: false, owner: null, expires_at: null },
    created_at: "2026-10-26T00:00:00Z",
    started_at: null,
    finished_at: null,
    error_code: null,
    error_message: null,
    manifest_path: null,
  };
}

describe("create task defaults", () => {
  it("allows a full completed task to provide a category subset", () => {
    expect(
      jobCanProvideFormScope(
        job({ mode: "all", manager_ids: [], categories: [], fund_codes: [] }),
        "category",
        "",
        "权益",
        "",
      ),
    ).toBe(true);
  });

  it("only exposes same-selector subsets for non-full source tasks", () => {
    const source = job({
      mode: "category",
      manager_ids: [],
      categories: ["权益"],
      fund_codes: [],
    });
    expect(jobCanProvideFormScope(source, "category", "", "权益", "")).toBe(true);
    expect(jobCanProvideFormScope(source, "category", "", "固收", "")).toBe(false);
    expect(jobCanProvideFormScope(source, "manager", "manager-1", "", "")).toBe(false);
  });

  it("defaults to all funds and download when no job exists", () => {
    expect(taskDefaultsFromJob(null)).toEqual({
      scopeMode: "all",
      managerId: "",
      category: "",
      fundCode: "",
      stages: ["download"],
    });
  });

  it("restores the latest task scope and stages", () => {
    expect(
      taskDefaultsFromJob(
        job(
          {
            mode: "manager",
            manager_ids: ["manager-1"],
            categories: [],
            fund_codes: [],
          },
          ["structure", "parse"],
        ),
      ),
    ).toEqual({
      scopeMode: "manager",
      managerId: "manager-1",
      category: "",
      fundCode: "",
      stages: ["parse", "structure"],
    });
  });

  it("canonicalizes arbitrary stage selection order", () => {
    expect(orderedStages(["structure", "download", "parse"])).toEqual([
      "download",
      "parse",
      "structure",
    ]);
  });

  it("restores fund and category scopes", () => {
    expect(
      taskDefaultsFromJob(
        job({
          mode: "funds",
          manager_ids: [],
          categories: [],
          fund_codes: ["AAA001"],
        }),
      ).fundCode,
    ).toBe("AAA001");
    expect(
      taskDefaultsFromJob(
        job({
          mode: "category",
          manager_ids: [],
          categories: ["固收"],
          fund_codes: [],
        }),
      ).category,
    ).toBe("固收");
  });
});

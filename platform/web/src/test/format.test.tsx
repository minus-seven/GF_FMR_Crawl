import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { QualityBadge, JobBadge } from "../components/StatusBadge";
import { EmptyState, ErrorState, LoadingState } from "../components/States";
import {
  eventSummary,
  formatEventDateTime,
  formatNumber,
  isTerminalJob,
  jobStatusLabel,
  qualityLabel,
} from "../lib/format";

describe("display formatting", () => {
  it("keeps missing numeric values explicit", () => {
    expect(formatNumber(null)).toBe("未披露");
    expect(formatNumber(12.345, 2)).toBe("12.35");
  });

  it("exposes stable Chinese quality and task labels", () => {
    expect(qualityLabel("validation_warning")).toBe("待复核");
    expect(jobStatusLabel("waiting_lock")).toBe("等待任务锁");
    expect(isTerminalJob("completed")).toBe(true);
    expect(isTerminalJob("running")).toBe(false);
  });

  it("formats replay event timestamps with seconds", () => {
    expect(formatEventDateTime("2026-10-26T08:12:34Z")).toMatch(
      /\d{2}:\d{2}:\d{2}/,
    );
  });

  it("renders visible quality and task states", () => {
    render(
      <>
        <QualityBadge status="missing_metric" />
        <JobBadge status="running" />
      </>,
    );
    expect(screen.getByText("缺少指标")).toBeInTheDocument();
    expect(screen.getByText("运行中")).toBeInTheDocument();
  });

  it("renders loading, empty and request-scoped error states", () => {
    render(
      <>
        <LoadingState label="正在加载基金列表" />
        <EmptyState title="没有质量问题" />
        <ErrorState message="基金不存在" requestId="req_test" />
      </>,
    );
    expect(screen.getByText("正在加载基金列表")).toBeInTheDocument();
    expect(screen.getByText("没有质量问题")).toBeInTheDocument();
    expect(screen.getByText("基金不存在")).toBeInTheDocument();
    expect(screen.getByText("req_test")).toBeInTheDocument();
  });

  it("renders event states with consistent English labels", () => {
    expect(
      eventSummary({
        type: "job.backfill",
        payload: { status: "running", sheet: "摩根", rows: [28] },
      } as never),
    ).toBe("RUNNING · 摩根 · 1 row");
    expect(
      eventSummary({
        type: "job.stage",
        payload: { stage: "download", status: "running" },
      } as never),
    ).toBe("DOWNLOAD · RUNNING");
    expect(
      eventSummary({
        type: "job.stage",
        payload: { stage: "parse", status: "running", processed: 14, total: 45 },
      } as never),
    ).toBe("PARSE · RUNNING · 14/45");
    expect(
      eventSummary({
        type: "job.stage",
        payload: { stage: "queued", status: "queued" },
      } as never),
    ).toBe("QUEUED");
    expect(
      eventSummary({
        type: "job.fund",
        payload: { status: "success", fund_code: "AGD276" },
      } as never),
    ).toBe("SUCCEEDED");
    expect(
      eventSummary({
        type: "job.retry",
        payload: {
          phase: "source_fallback",
          candidate_rank: 1,
          next_candidate_rank: 2,
          reason: "HTTP 404",
        },
      } as never),
    ).toBe("SOURCE 1 → 2 · HTTP 404");
  });
});

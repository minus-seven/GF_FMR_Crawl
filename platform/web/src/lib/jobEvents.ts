import type { Job, JobEvent, JobStatus } from "../api/types";

const jobStatuses = new Set<JobStatus>([
  "waiting_lock",
  "queued",
  "running",
  "retrying",
  "validating",
  "review_required",
  "publishing",
  "completed",
  "failed",
  "cancelled",
  "publish_failed",
]);

function eventStatus(value: unknown, fallback: JobStatus): JobStatus {
  const status = String(value ?? "") as JobStatus;
  return jobStatuses.has(status) ? status : fallback;
}

export function mergeJobEvent(job: Job, event: JobEvent): Job {
  const payload = event.payload;
  if (event.type === "job.completed") {
    const status = eventStatus(payload.status, "completed");
    return {
      ...job,
      status,
      current_stage: null,
      progress:
        status === "completed"
          ? { ...job.progress, processed: job.progress.total }
          : job.progress,
    };
  }
  if (event.type === "job.stage") {
    const stage = String(payload.stage ?? job.current_stage ?? "");
    const phase = String(payload.status ?? "");
    const processed = Number(payload.processed);
    const total = Number(payload.total);
    const hasProgress =
      Number.isFinite(processed)
      && Number.isFinite(total)
      && total > 0;
    const status =
      stage === "queued"
        ? "queued"
        : stage === "validating"
          ? "validating"
          : phase === "running"
            ? "running"
            : job.status;
    return {
      ...job,
      status,
      current_stage: stage || job.current_stage,
      progress: hasProgress
        ? {
            ...job.progress,
            processed: Math.max(0, Math.min(processed, total)),
            total,
          }
        : job.progress,
    };
  }
  if (event.type === "job.backfill" && payload.stage === "master_database") {
    const processed = Number(payload.processed);
    const total = Number(payload.total);
    if (Number.isFinite(processed) && Number.isFinite(total) && total > 0) {
      return {
        ...job,
        progress: {
          ...job.progress,
          processed: Math.max(0, Math.min(processed, total)),
          total,
        },
      };
    }
  }
  if (event.type === "job.warning") {
    return {
      ...job,
      status: eventStatus(payload.status, job.status),
    };
  }
  if (event.type === "job.snapshot") {
    return {
      ...job,
      status: eventStatus(payload.status, job.status),
    };
  }
  if (event.type === "job.resume") {
    return {
      ...job,
      status: "queued",
      current_stage: null,
      cancellation_requested: false,
      error_code: null,
      error_message: null,
      finished_at: null,
    };
  }
  if (event.type === "job.publish") {
    return {
      ...job,
      status: eventStatus(payload.status, "publishing"),
    };
  }
  return job;
}

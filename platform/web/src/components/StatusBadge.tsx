import { AlertTriangle, CheckCircle2, CircleDashed, FileQuestion } from "lucide-react";
import type { JobStatus, QualityStatus } from "../api/types";
import { jobStatusLabel, qualityLabel } from "../lib/format";

export function QualityBadge({ status }: { status: QualityStatus }) {
  const Icon =
    status === "complete"
      ? CheckCircle2
      : status === "validation_warning" || status === "review_required"
        ? AlertTriangle
        : FileQuestion;
  return (
    <span className={`status-badge quality-${status}`}>
      <Icon size={13} />
      {qualityLabel(status)}
    </span>
  );
}

export function JobBadge({ status }: { status: JobStatus }) {
  const active = ["waiting_lock", "queued", "running", "retrying", "validating", "publishing"].includes(status);
  return (
    <span className={`status-badge job-${status}`}>
      {active ? <CircleDashed size={13} className="spin" /> : null}
      {jobStatusLabel(status)}
    </span>
  );
}

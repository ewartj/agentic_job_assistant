import type { RunStatus } from "../api/client";

const LABELS: Record<RunStatus, string> = {
  running: "Working",
  awaiting_review: "Ready for review",
  stopped: "Stopped",
  saved: "Saved",
  rejected: "Rejected",
};

export function StatusBadge({ status }: { status: RunStatus }) {
  return <span className={`badge badge-${status}`}>{LABELS[status]}</span>;
}

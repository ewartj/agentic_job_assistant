import type { ApplicationStatus } from "../api/client";

/** In the order an application moves through them. */
export const APPLICATION_STATUSES: Record<ApplicationStatus, string> = {
  interested: "Interested",
  applied: "Applied",
  interviewing: "Interviewing",
  offer: "Offer",
  rejected: "Turned down", // by them; you not applying is "withdrawn"
  withdrawn: "Withdrawn",
};

/** Statuses an application only reaches once it's been sent. */
const SENT: ReadonlySet<ApplicationStatus> = new Set(["applied", "interviewing", "offer", "rejected"]);

/** Whether you sent this application, whatever happened next. Withdrawn ones count only if
 * they were sent first (the tracker records the date you applied). */
export function hasApplied(application: { status: ApplicationStatus; applied_on?: string | null }): boolean {
  return Boolean(application.applied_on) || SENT.has(application.status);
}

export function ApplicationStatusBadge({ status }: { status: ApplicationStatus }) {
  return <span className={`badge app-${status}`}>{APPLICATION_STATUSES[status]}</span>;
}

/** The day of a timestamp from the tracker ("2026-11-21T10:00:00+00:00" -> "2026-11-21"). */
export function day(timestamp: string): string {
  return timestamp.slice(0, 10);
}

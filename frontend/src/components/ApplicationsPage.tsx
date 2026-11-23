import { useCallback, useEffect, useState } from "react";
import { api, unwrap, type Application, type ApplicationStatus } from "../api/client";
import { applicationHref } from "../routes";
import { APPLICATION_STATUSES, ApplicationStatusBadge, day, hasApplied } from "./ApplicationStatusBadge";
import { LoadError } from "./LoadError";

/** Every application, those you've sent (whatever came of them), or those with one status. */
type Filter = "all" | "sent" | ApplicationStatus;

/** The tracker: every application, most recently updated first, optionally of one status. */
export function ApplicationsPage() {
  const [applications, setApplications] = useState<Application[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<Filter>("all");

  const load = useCallback(() => {
    setLoading(true);
    api
      .GET("/api/applications")
      .then((r) => {
        setApplications(unwrap(r));
        setError(null);
      })
      .catch((e: unknown) => setError(`Couldn't load the applications (${e instanceof Error ? e.message : String(e)}).`))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  const shown = applications?.filter(
    (a) => status === "all" || (status === "sent" ? hasApplied(a) : a.status === status),
  );

  return (
    <div className="stack">
      <h1>Applications</h1>
      {error && <LoadError error={error} loading={loading} onRetry={load} />}
      <section className="card stack">
        <label className="inline">
          Show{" "}
          <select value={status} onChange={(e) => setStatus(e.target.value as Filter)}>
            <option value="all">All</option>
            <option value="sent">Applied for (any outcome)</option>
            {Object.entries(APPLICATION_STATUSES).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        {!shown ? (
          <p className="muted">{error ? "Not loaded." : "Loading..."}</p>
        ) : shown.length === 0 ? (
          <p className="muted">{applications?.length ? "None with that status." : "None yet: approve a run to add one."}</p>
        ) : (
          <ul className="list">
            {shown.map((a) => (
              <li key={a.id}>
                <a href={applicationHref(a.id)}>
                  {a.role} at {a.company}
                </a>
                <span className="muted small">updated {day(a.updated_at)}</span>
                <ApplicationStatusBadge status={a.status} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

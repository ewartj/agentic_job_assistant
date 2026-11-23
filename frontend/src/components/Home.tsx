import type { RunView } from "../api/client";
import { useJobs } from "../jobs/useJobs";
import { isNew } from "../postings";
import { JOBS, runHref } from "../routes";
import { JobList } from "./JobList";
import { LoadError } from "./LoadError";
import { NewRunForm } from "./NewRunForm";
import { StatusBadge } from "./StatusBadge";

/** Runs that need you, or are still going; finished ones are on the Saved jobs page. */
const OPEN: ReadonlySet<RunView["status"]> = new Set(["running", "awaiting_review", "stopped"]);

/** The start page: paste a new job, pick up a run that needs you, or assess a job not yet looked at. */
export function Home() {
  const { jobs, runs, error, loading, reload, start, setArchived } = useJobs();
  // Not loaded yet: "Loading...", unless loading failed - then the error above says why.
  const pending = <p className="muted">{error ? "Not loaded." : "Loading..."}</p>;
  const open = runs?.filter((r) => OPEN.has(r.status));

  return (
    <div className="stack">
      {error && <LoadError error={error} loading={loading} onRetry={reload} />}
      <NewRunForm onStart={start} />
      <section className="card stack">
        <h2>Needs you</h2>
        {!open ? (
          pending
        ) : open.length === 0 ? (
          <p className="muted">Nothing waiting: no runs going, to review or stopped.</p>
        ) : (
          <ul className="list">
            {open.map((run) => (
              <li key={run.run_id}>
                <a href={runHref(run.run_id)}>{run.review ? `${run.review.role} at ${run.review.company}` : run.posting}</a>
                <span className="muted small">{run.run_id}</span>
                <StatusBadge status={run.status} />
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="card stack">
        <div className="row spread">
          <h2>New jobs</h2>
          <a href={JOBS}>All saved jobs</a>
        </div>
        {!jobs ? (
          pending
        ) : (
          <JobList jobs={jobs.filter(isNew)} empty="None: every saved job has been assessed or archived." onStart={start} onArchive={setArchived} />
        )}
      </section>
    </div>
  );
}

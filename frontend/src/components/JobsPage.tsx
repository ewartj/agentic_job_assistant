import { useState } from "react";
import { useJobs } from "../jobs/useJobs";
import { JobList } from "./JobList";
import { LoadError } from "./LoadError";

/** Every saved job, with how its latest run went. Archived ones are hidden unless asked for. */
export function JobsPage() {
  const { jobs, error, loading, reload, start, setArchived } = useJobs();
  const [showArchived, setShowArchived] = useState(false);
  const archived = jobs?.filter((j) => j.archived).length ?? 0;
  const shown = jobs?.filter((j) => showArchived || !j.archived);

  return (
    <div className="stack">
      <h1>Saved jobs</h1>
      {error && <LoadError error={error} loading={loading} onRetry={reload} />}
      <section className="card stack">
        <label className="check">
          <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
          Show archived ({archived})
        </label>
        {!shown ? (
          <p className="muted">{error ? "Not loaded." : "Loading..."}</p>
        ) : (
          <JobList jobs={shown} empty="No saved jobs yet." onStart={start} onArchive={setArchived} />
        )}
      </section>
    </div>
  );
}

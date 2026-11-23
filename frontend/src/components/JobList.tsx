import { useState } from "react";
import { api, unwrap } from "../api/client";
import type { Job } from "../postings";
import { runHref } from "../routes";
import { StatusBadge } from "./StatusBadge";

interface Props {
  jobs: Job[];
  /** Shown when there are no jobs. */
  empty: string;
  onStart: (posting: string) => Promise<void>;
  onArchive: (posting: string, archived: boolean) => Promise<void>;
}

/** Saved jobs: each with how its latest run went, its advert, and buttons to assess or archive it. */
export function JobList({ jobs, empty, onStart, onArchive }: Props) {
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState<string | null>(null); // the job a button is busy with

  async function act(name: string, action: () => Promise<void>) {
    setWorking(name);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setWorking(null);
    }
  }

  if (jobs.length === 0) return <p className="muted">{empty}</p>;
  return (
    <>
      {error && <p className="error" role="alert">{error}</p>}
      <ul className="list">
        {jobs.map((job) => {
          const title = job.role && job.company ? `${job.role} at ${job.company}` : job.name;
          return (
            <li key={job.name} className="job">
              <div className="job-head">
                <span>
                  {title}
                  {job.saved && <span className="muted small"> saved {job.saved}</span>}
                </span>
                {job.latest ? (
                  <a href={runHref(job.latest.run_id)} aria-label={`Latest run of ${title}`}>
                    <StatusBadge status={job.latest.status} />
                  </a>
                ) : (
                  <span className="muted small">Not assessed</span>
                )}
                <span className="row">
                  <button
                    type="button"
                    onClick={() => void act(job.name, () => onStart(job.name))}
                    disabled={working !== null}
                  >
                    {working === job.name ? "Working..." : job.latest ? "Assess again" : "Assess"}
                  </button>
                  <button
                    type="button"
                    onClick={() => void act(job.name, () => onArchive(job.name, !job.archived))}
                    disabled={working !== null}
                  >
                    {job.archived ? "Bring back" : "Archive"}
                  </button>
                </span>
              </div>
              <Advert name={job.name} />
            </li>
          );
        })}
      </ul>
    </>
  );
}

/** The advert's text, fetched the first time it's opened. */
function Advert({ name }: { name: string }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    if (text !== null) return;
    try {
      setText(unwrap(await api.GET("/api/postings/{name}", { params: { path: { name } } })).text);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <details className="advert" onToggle={(e) => e.currentTarget.open && void load()}>
      <summary>The advert</summary>
      {error ? <p className="error">{error}</p> : text === null ? <p className="muted">Loading...</p> : <p className="advert-text">{text}</p>}
    </details>
  );
}

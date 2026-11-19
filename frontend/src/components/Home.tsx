import { useCallback, useEffect, useState } from "react";
import { api, unwrap, type PostingSummary, type RunView } from "../api/client";
import { runHref } from "../routes";
import { NewRunForm } from "./NewRunForm";
import { StatusBadge } from "./StatusBadge";

/** The start page: paste a new job, run a saved one, or open an earlier run. */
export function Home() {
  const [postings, setPostings] = useState<PostingSummary[] | null>(null);
  const [runs, setRuns] = useState<RunView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // An earlier error stays on screen until this attempt ends, so the "Try again" button
  // (and keyboard focus on it) isn't pulled away mid-retry.
  const load = useCallback(() => {
    setLoading(true);
    Promise.all([api.GET("/api/postings"), api.GET("/api/runs")])
      .then(([p, r]) => {
        setPostings(unwrap(p));
        setRuns(unwrap(r));
        setError(null);
      })
      .catch((e: unknown) =>
        setError(`Couldn't reach job-web (${e instanceof Error ? e.message : String(e)}). Is it running?`),
      )
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  // Not loaded yet: "Loading...", unless loading failed - then the error above says why.
  const pending = error ? "Not loaded." : "Loading...";

  async function start(posting: string) {
    const run = unwrap(await api.POST("/api/runs", { body: { posting } }));
    window.location.hash = runHref(run.run_id);
  }

  return (
    <div className="stack">
      {error && (
        <div className="row" role="alert">
          <p className="error">{error}</p>
          <button type="button" onClick={() => loading || load()} aria-disabled={loading}>
            {loading ? "Trying again..." : "Try again"}
          </button>
        </div>
      )}
      <NewRunForm onStart={start} />
      <section className="card stack">
        <h2>Runs</h2>
        {runs === null ? (
          <p className="muted">{pending}</p>
        ) : runs.length === 0 ? (
          <p className="muted">No runs yet.</p>
        ) : (
          <ul className="list">
            {runs.map((run) => (
              <li key={run.run_id}>
                <a href={runHref(run.run_id)}>{run.review ? `${run.review.role} at ${run.review.company}` : run.posting}</a>
                <span className="muted small">{run.run_id}</span>
                <StatusBadge status={run.status} />
              </li>
            ))}
          </ul>
        )}
      </section>
      <SavedPostings postings={postings} pending={pending} onStart={start} />
    </div>
  );
}

interface SavedPostingsProps {
  postings: PostingSummary[] | null;
  /** What to show while postings is null. */
  pending: string;
  onStart: (name: string) => Promise<void>;
}

function SavedPostings({ postings, pending, onStart }: SavedPostingsProps) {
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState<string | null>(null);

  async function run(name: string) {
    setStarting(name);
    setError(null);
    try {
      await onStart(name);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStarting(null);
    }
  }

  return (
    <section className="card stack">
      <h2>Saved jobs</h2>
      {error && <p className="error" role="alert">{error}</p>}
      {postings === null ? (
        <p className="muted">{pending}</p>
      ) : postings.length === 0 ? (
        <p className="muted">None saved yet.</p>
      ) : (
        <ul className="list">
          {postings.map((p) => (
            <li key={p.name}>
              <span>
                {p.role && p.company ? `${p.role} at ${p.company}` : p.name}
                {p.saved && <span className="muted small"> saved {p.saved}</span>}
              </span>
              <button type="button" onClick={() => void run(p.name)} disabled={starting !== null}>
                {starting === p.name ? "Starting..." : "Assess"}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

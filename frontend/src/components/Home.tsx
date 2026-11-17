import { useEffect, useState } from "react";
import { api, unwrap, type PostingSummary, type RunView } from "../api/client";
import { runHref } from "../routes";
import { NewRunForm } from "./NewRunForm";
import { StatusBadge } from "./StatusBadge";

/** The start page: paste a new job, run a saved one, or open an earlier run. */
export function Home() {
  const [postings, setPostings] = useState<PostingSummary[] | null>(null);
  const [runs, setRuns] = useState<RunView[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.GET("/api/postings"), api.GET("/api/runs")])
      .then(([p, r]) => {
        setPostings(unwrap(p));
        setRuns(unwrap(r));
      })
      .catch((e: unknown) =>
        setError(`Couldn't reach job-web (${e instanceof Error ? e.message : String(e)}). Is it running?`),
      );
  }, []);

  async function start(posting: string) {
    const run = unwrap(await api.POST("/api/runs", { body: { posting } }));
    window.location.hash = runHref(run.run_id);
  }

  return (
    <div className="stack">
      {error && <p className="error" role="alert">{error}</p>}
      <NewRunForm onStart={start} />
      <section className="card stack">
        <h2>Runs</h2>
        {runs === null ? (
          <p className="muted">Loading...</p>
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
      <SavedPostings postings={postings} onStart={start} />
    </div>
  );
}

function SavedPostings({ postings, onStart }: { postings: PostingSummary[] | null; onStart: (name: string) => Promise<void> }) {
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
        <p className="muted">Loading...</p>
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

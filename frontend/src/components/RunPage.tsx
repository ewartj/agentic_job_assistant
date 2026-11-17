import { useRun } from "../run/useRun";
import { DecisionForm } from "./DecisionForm";
import { ProgressPanel } from "./ProgressPanel";
import { ReviewPanel } from "./ReviewPanel";
import { StatusBadge } from "./StatusBadge";

/** One run: its progress while it works, the review when it pauses, and how it ended. */
export function RunPage({ runId }: { runId: string }) {
  const { view, progress, error, busy, decide, retry } = useRun(runId);

  return (
    <div className="stack">
      <p>
        <a href="#/">← All jobs</a>
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      {view && (
        <>
          <header className="row spread">
            <h1>{view.review ? `${view.review.role} at ${view.review.company}` : view.posting}</h1>
            <StatusBadge status={view.status} />
          </header>
          {view.status === "running" && <ProgressPanel progress={progress} />}
          {view.status === "stopped" && (
            <section className="card stack">
              <p className="error">Stopped part-way{view.error ? `: ${view.error}` : "."}</p>
              <p className="muted">Retrying carries on from the last saved step; nothing done so far is lost.</p>
              <div>
                <button type="button" className="primary" disabled={busy} onClick={() => void retry()}>
                  Retry
                </button>
              </div>
            </section>
          )}
          {view.status === "saved" && (
            <p className="card ok">Saved to the tracker as application {view.application_id}.</p>
          )}
          {view.status === "rejected" && <p className="card muted">Rejected: nothing was saved.</p>}
          {view.review && <ReviewPanel review={view.review} runId={view.run_id} />}
          {view.status === "awaiting_review" && <DecisionForm onDecide={decide} busy={busy} />}
        </>
      )}
      {!view && !error && <p className="muted">Loading...</p>}
    </div>
  );
}

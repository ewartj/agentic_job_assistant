import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  api,
  unwrap,
  type ApplicationDetail,
  type ApplicationStatus,
  type Draft,
  type DraftKind,
  type DraftSummary,
} from "../api/client";
import { APPLICATIONS } from "../routes";
import { APPLICATION_STATUSES, ApplicationStatusBadge, day } from "./ApplicationStatusBadge";
import { LoadError } from "./LoadError";
import { Markdown } from "./Markdown";

const DRAFT_TITLES: Record<DraftKind, string> = {
  assessment: "Assessment",
  cover_letter_plan: "Letter plan",
  cv: "CV plan",
  cover_letter: "Cover letter",
  motivation: "Motivation",
  application_answers: "Application answers",
};

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** One application: where it stands, how it got there, moving it on, and its drafts. */
export function ApplicationPage({ applicationId }: { applicationId: number }) {
  const [application, setApplication] = useState<ApplicationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const path = { params: { path: { application_id: applicationId } } };

  const load = useCallback(() => {
    setLoading(true);
    api
      .GET("/api/applications/{application_id}", { params: { path: { application_id: applicationId } } })
      .then((r) => {
        setApplication(unwrap(r));
        setError(null);
      })
      .catch((e: unknown) => setError(`Couldn't load the application (${message(e)}).`))
      .finally(() => setLoading(false));
  }, [applicationId]);

  useEffect(load, [load]);

  async function move(status: ApplicationStatus, note: string) {
    setApplication(unwrap(await api.POST("/api/applications/{application_id}/status", { ...path, body: { status, note: note || null } })));
  }

  return (
    <div className="stack">
      <p>
        <a href={APPLICATIONS}>← All applications</a>
      </p>
      {error && <LoadError error={error} loading={loading} onRetry={load} />}
      {!application && !error && <p className="muted">Loading...</p>}
      {application && (
        <>
          <header className="row spread">
            <h1>
              {application.role} at {application.company}
            </h1>
            <ApplicationStatusBadge status={application.status} />
          </header>
          <p className="row small">
            {application.applied_on && <span>Applied {application.applied_on}</span>}
            {application.url && (
              <a href={application.url} target="_blank" rel="noopener noreferrer">
                The advert
              </a>
            )}
            {application.posting && <span className="muted">Saved job: {application.posting}</span>}
          </p>
          {application.notes && <p className="card">{application.notes}</p>}
          <StatusForm current={application.status} onMove={move} />
          <section className="card stack">
            <h2>History</h2>
            <ol className="list">
              {application.history.map((h, i) => (
                <li key={i}>
                  <ApplicationStatusBadge status={h.status} />
                  <span>{h.note}</span>
                  <span className="muted small">{day(h.changed_at)}</span>
                </li>
              ))}
            </ol>
          </section>
          <Drafts applicationId={applicationId} drafts={application.drafts} />
        </>
      )}
    </div>
  );
}

function StatusForm({ current, onMove }: { current: ApplicationStatus; onMove: (s: ApplicationStatus, note: string) => Promise<void> }) {
  const [status, setStatus] = useState<ApplicationStatus>(current);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => setStatus(current), [current]); // after a move, start from where it now is

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onMove(status, note);
      setNote("");
    } catch (e) {
      setError(`That didn't go through: ${message(e)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <h2>Move it on</h2>
      <div className="row">
        <label>
          New status
          <select value={status} onChange={(e) => setStatus(e.target.value as ApplicationStatus)}>
            {Object.entries(APPLICATION_STATUSES).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Note <span className="muted">(optional)</span>
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="First interview on 3 Dec" />
        </label>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      <div>
        <button type="submit" className="primary" disabled={busy || status === current}>
          {busy ? "Saving..." : "Save"}
        </button>
      </div>
    </form>
  );
}

/** The drafts saved with the application: pick one (any version) to read it. */
function Drafts({ applicationId, drafts }: { applicationId: number; drafts: DraftSummary[] }) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function open(d: DraftSummary) {
    setError(null);
    try {
      setDraft(
        unwrap(
          await api.GET("/api/applications/{application_id}/drafts/{kind}", {
            params: { path: { application_id: applicationId, kind: d.kind }, query: { version: d.version } },
          }),
        ),
      );
    } catch (e) {
      setError(message(e));
    }
  }

  return (
    <section className="card stack">
      <h2>Drafts</h2>
      {drafts.length === 0 ? (
        <p className="muted">None saved.</p>
      ) : (
        <ul className="list">
          {drafts.map((d) => {
            const title = `${DRAFT_TITLES[d.kind]}, version ${d.version}`;
            const showing = draft?.kind === d.kind && draft.version === d.version;
            return (
              <li key={d.id}>
                <span>{title}</span>
                <span className="muted small">{day(d.created_at)}</span>
                <button type="button" aria-pressed={showing} onClick={() => void open(d)}>
                  {showing ? "Showing" : "Read"}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      {draft && (
        <article className="stack" aria-label={`${DRAFT_TITLES[draft.kind]}, version ${draft.version}`}>
          <h3>
            {DRAFT_TITLES[draft.kind]}, version {draft.version}
          </h3>
          <Markdown text={draft.text} />
        </article>
      )}
    </section>
  );
}

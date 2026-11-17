import { useState, type FormEvent } from "react";
import { ApiError, api, unwrap } from "../api/client";
import { NAME, suggestName } from "../postings";

interface Props {
  /** Starts a run of a saved posting; resolves once it has started. */
  onStart: (posting: string) => Promise<void>;
}

/** Paste a job advert: it's saved as a posting, then a run starts on it. */
export function NewRunForm({ onStart }: Props) {
  const [company, setCompany] = useState("");
  const [role, setRole] = useState("");
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [name, setName] = useState("");
  const [nameEdited, setNameEdited] = useState(false);
  const [replace, setReplace] = useState(false);
  const [exists, setExists] = useState(false);
  const [savedAs, setSavedAs] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Any change to what's being saved means saving it again.
  const edit = (set: (value: string) => void) => (e: { target: { value: string } }) => {
    set(e.target.value);
    setSavedAs(null);
  };

  const shownName = nameEdited ? name : suggestName(company, role);
  const nameProblem = shownName && !NAME.test(shownName) ? "Use letters, digits, _ and - only" : null;
  const canSubmit = Boolean(shownName && text.trim() && !nameProblem && !busy);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      // Saved already (the run failed to start last time)? Then just try the run again.
      if (savedAs !== shownName) {
        unwrap(
          await api.POST("/api/postings", {
            body: { name: shownName, text, company: company || null, role: role || null, url: url || null, replace },
          }),
        );
        setSavedAs(shownName);
      }
      await onStart(shownName);
    } catch (e) {
      setExists(e instanceof ApiError && e.status === 409);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <h2>New job</h2>
      <div className="row">
        <label>
          Company
          <input value={company} onChange={edit(setCompany)} placeholder="Acme Health" />
        </label>
        <label>
          Role
          <input value={role} onChange={edit(setRole)} placeholder="Data Scientist" />
        </label>
      </div>
      <label>
        Link to the advert <span className="muted">(optional)</span>
        <input type="url" value={url} onChange={edit(setUrl)} placeholder="https://..." />
      </label>
      <label>
        The advert
        <textarea value={text} onChange={edit(setText)} rows={12} placeholder="Paste the job advert here" />
      </label>
      <label>
        Save as
        <input
          value={shownName}
          onChange={(e) => {
            setName(e.target.value);
            setNameEdited(true);
          }}
          placeholder="acme_health_data_scientist"
          aria-invalid={nameProblem ? true : undefined}
        />
        {nameProblem && <span className="field-error">{nameProblem}</span>}
      </label>
      {exists && (
        <label className="check">
          <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
          Replace the saved posting with this text
        </label>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      <div>
        <button type="submit" className="primary" disabled={!canSubmit}>
          {busy ? "Starting..." : "Save and assess"}
        </button>
      </div>
    </form>
  );
}

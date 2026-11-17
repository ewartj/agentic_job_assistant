import { useState } from "react";
import type { Decision } from "../api/client";

type Target = Decision["target"];

interface Props {
  onDecide: (decision: Decision) => Promise<void>;
  busy: boolean;
}

/**
 * Approve (saved to the tracker), revise with notes (redrafted, checked and shown
 * again), or reject (nothing saved). Rejecting asks first, since it can't be undone.
 */
export function DecisionForm({ onDecide, busy }: Props) {
  const [notes, setNotes] = useState("");
  const [target, setTarget] = useState<Target>("both");
  const [confirmReject, setConfirmReject] = useState(false);

  const decide = (action: Decision["action"]) => void onDecide({ action, notes: notes.trim(), target });

  return (
    <section className="card stack">
      <h2>Your decision</h2>
      <label>
        What to change <span className="muted">(needed to revise)</span>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={3}
          placeholder="e.g. Lead with the cloud work, and drop the point about mentoring"
        />
      </label>
      <label>
        Revise
        <select value={target} onChange={(e) => setTarget(e.target.value as Target)}>
          <option value="both">Both</option>
          <option value="cover_letter_plan">The letter plan only</option>
          <option value="cv">The CV only</option>
        </select>
      </label>
      <div className="row buttons">
        <button type="button" className="primary" disabled={busy} onClick={() => decide("approve")}>
          Approve and save
        </button>
        <button type="button" disabled={busy || !notes.trim()} onClick={() => decide("revise")}>
          Revise
        </button>
        {confirmReject ? (
          <span className="row">
            <button type="button" className="danger" disabled={busy} onClick={() => decide("reject")}>
              Yes, reject
            </button>
            <button type="button" onClick={() => setConfirmReject(false)}>
              Cancel
            </button>
          </span>
        ) : (
          <button type="button" disabled={busy} onClick={() => setConfirmReject(true)}>
            Reject
          </button>
        )}
      </div>
    </section>
  );
}

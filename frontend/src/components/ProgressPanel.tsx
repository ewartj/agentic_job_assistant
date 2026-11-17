import { PART_LABELS, PARTS, checkCounts, summary, type PartProgress, type Progress } from "../run/progress";

const VERDICTS: Record<string, string> = { met: "Met", partly: "Partly met", gap: "Not met", error: "Not judged" };

/** What the run is doing: the shared assessment, then the letter plan and CV side by side. */
export function ProgressPanel({ progress }: { progress: Progress }) {
  const started = PARTS.some((part) => progress[part].started);
  return (
    <section className="card stack" aria-live="polite">
      <h2>
        <span className="spinner" aria-hidden="true" /> Working
      </h2>
      {!started && <p className="muted">Starting... The first progress can take a minute with a local model.</p>}
      <div className="parts">
        {PARTS.map((part) => (
          <PartCard key={part} label={PART_LABELS[part]} part={progress[part]} />
        ))}
      </div>
    </section>
  );
}

function PartCard({ label, part }: { label: string; part: PartProgress }) {
  const unsupported = part.checks.filter((c) => c.supported === false);
  const { unchecked } = checkCounts(part);
  return (
    <div className={`part ${part.started ? "" : "part-waiting"}`}>
      <h3>{label}</h3>
      <p className="small">
        {summary(part)}
        {part.elapsed > 0 && <span className="muted"> · {Math.round(part.elapsed)}s</span>}
      </p>
      {part.matched.length > 0 && (
        <ul className="verdicts">
          {part.matched.map((m) => (
            <li key={m.requirement}>
              <span className={`verdict verdict-${m.verdict}`}>{VERDICTS[m.verdict] ?? m.verdict}</span>
              {m.requirement}
              <span className="muted small"> ({m.kind})</span>
            </li>
          ))}
        </ul>
      )}
      {unsupported.length > 0 && (
        <ul className="problems">
          {unsupported.map((c, i) => (
            <li key={i}>
              <q>{c.point}</q> <span className="muted small">{c.cv_ids.join(", ")}</span>
              <div className="small">{c.problem}</div>
            </li>
          ))}
        </ul>
      )}
      {unchecked > 0 && <p className="muted small">{unchecked} point(s) couldn't be checked.</p>}
    </div>
  );
}

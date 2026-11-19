import { useId, useState, type KeyboardEvent, type ReactNode } from "react";
import { fileUrl, type Artifact, type Review } from "../api/client";
import { Markdown } from "./Markdown";

const TITLES: Record<Artifact["kind"], string> = { cover_letter_plan: "Letter plan", cv: "CV" };

/** The assessment, then the letter plan and the CV as tabs, each with how its checks went. */
type Tab = "assessment" | Artifact["kind"];

export function ReviewPanel({ review, runId }: { review: Review; runId: string }) {
  const [tab, setTab] = useState<Tab>("assessment");
  const artifact = review.artifacts.find((a) => a.kind === tab);
  const id = useId();
  const tabs: Tab[] = ["assessment", ...review.artifacts.map((a) => a.kind)];

  // The usual keys for tabs: arrows move along (wrapping round), Home and End go to the ends.
  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const moves: Record<string, number> = { ArrowRight: 1, ArrowLeft: -1 };
    const at = tabs.indexOf(tab);
    const next =
      event.key in moves ? (at + moves[event.key]! + tabs.length) % tabs.length
      : event.key === "Home" ? 0
      : event.key === "End" ? tabs.length - 1
      : null;
    if (next === null) return;
    event.preventDefault();
    setTab(tabs[next]!);
    document.getElementById(`${id}-tab-${tabs[next]}`)?.focus();
  }
  const tabProps = (kind: Tab) => ({
    id: `${id}-tab-${kind}`,
    panel: `${id}-panel`,
    active: tab === kind,
    onClick: () => setTab(kind),
  });
  // The API's keys (workflow/graph.py verdict_counts). The schema types them only as
  // dict[str, int], so these names are checked by ReviewPanel.test.tsx, not the compiler.
  const met = review.verdicts["met"] ?? 0;
  const partly = review.verdicts["partly met"] ?? 0;
  const gap = review.verdicts["not met"] ?? 0;

  return (
    <section className="card stack">
      <div className="row spread">
        <h2>Review{review.round > 1 && <span className="muted"> · round {review.round}</span>}</h2>
        <p className="small">
          <span className="verdict verdict-met">{met} met</span>{" "}
          <span className="verdict verdict-partly">{partly} partly</span>{" "}
          <span className="verdict verdict-gap">{gap} not met</span>
        </p>
      </div>
      <div className="tabs" role="tablist" aria-label="Review" onKeyDown={onKeyDown}>
        <TabButton {...tabProps("assessment")}>Assessment</TabButton>
        {review.artifacts.map((a) => (
          <TabButton key={a.kind} {...tabProps(a.kind)}>
            {TITLES[a.kind]} {a.verified ? "✓" : "⚠"}
          </TabButton>
        ))}
      </div>
      <div role="tabpanel" id={`${id}-panel`} aria-labelledby={`${id}-tab-${tab}`} tabIndex={0}>
        {artifact ? <ArtifactView artifact={artifact} runId={runId} /> : <Markdown text={review.assessment} />}
      </div>
    </section>
  );
}

interface TabButtonProps {
  id: string;
  /** The id of the panel it shows. */
  panel: string;
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}

function TabButton({ id, panel, active, onClick, children }: TabButtonProps) {
  return (
    <button
      type="button"
      role="tab"
      id={id}
      aria-selected={active}
      aria-controls={panel}
      tabIndex={active ? 0 : -1} // Tab goes to the chosen tab only; the arrows move between them
      className={active ? "tab active" : "tab"}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function ArtifactView({ artifact, runId }: { artifact: Artifact; runId: string }) {
  const isCv = artifact.kind === "cv";
  return (
    <div className="stack">
      <p className={artifact.verified ? "ok" : "warning"}>
        {artifact.verified
          ? `All ${artifact.points} points are supported by their cited CV entries.`
          : `${artifact.unsupported} of ${artifact.points} points aren't supported by their citations - check them before approving.`}{" "}
        <span className="muted small">
          {artifact.drafts} draft(s), {artifact.dropped} point(s) dropped
        </span>
      </p>
      {artifact.warnings && artifact.warnings.length > 0 && (
        <ul className="warning small">
          {artifact.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      <p className="row small">
        <a href={fileUrl(runId, isCv ? "cv_plan" : "cover_letter_plan")} download>
          Download the plan (.md)
        </a>
        {isCv && artifact.document_path && (
          <a href={fileUrl(runId, "cv")} download>
            Download the CV (.docx)
          </a>
        )}
      </p>
      <Markdown text={artifact.plan} />
    </div>
  );
}

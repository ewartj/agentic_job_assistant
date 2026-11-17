/**
 * A run's progress, built up from its events: what the shared assessment found, and how
 * each document's drafting and checking went. Pure functions, so they're tested on their own.
 */
import type { ProgressEvent } from "../api/client";

export type Part = "assessment" | "cover_letter_plan" | "cv";
export const PARTS: Part[] = ["assessment", "cover_letter_plan", "cv"];
export const PART_LABELS: Record<Part, string> = {
  assessment: "Assessment",
  cover_letter_plan: "Letter plan",
  cv: "CV",
};

type EventOf<S extends ProgressEvent["step"]> = Extract<ProgressEvent, { step: S }>;

export interface PartProgress {
  started: boolean;
  model?: string;
  requirements?: number;
  matched: EventOf<"matched">[];
  attempt: number; // drafts written so far
  toCheck: number; // points the latest draft sent for checking
  checks: EventOf<"checked">[]; // checks of the latest draft
  elapsed: number;
}

export type Progress = Record<Part, PartProgress>;

function emptyPart(): PartProgress {
  return { started: false, matched: [], attempt: 0, toCheck: 0, checks: [], elapsed: 0 };
}

export function emptyProgress(): Progress {
  return { assessment: emptyPart(), cover_letter_plan: emptyPart(), cv: emptyPart() };
}

/** Which part an event is about: the shared assessment has no document. */
export function partOf(event: ProgressEvent): Part {
  return event.artifact ?? "assessment";
}

export function addEvent(progress: Progress, event: ProgressEvent): Progress {
  const part = partOf(event);
  const before = progress[part];
  const now = { ...before, elapsed: event.elapsed ?? before.elapsed };
  switch (event.step) {
    case "started":
      Object.assign(now, { started: true, model: event.model });
      break;
    case "extracted":
      Object.assign(now, { started: true, requirements: event.requirements });
      break;
    case "matched":
      now.matched = [...before.matched, event];
      break;
    case "drafted":
      Object.assign(now, { attempt: event.attempt, toCheck: event.to_check, checks: [] });
      break;
    case "checked":
      now.checks = [...before.checks, event];
      break;
  }
  return { ...progress, [part]: now };
}

/** The latest draft's checks: passed, unsupported, and ones the checker couldn't finish. */
export function checkCounts(part: PartProgress): { passed: number; unsupported: number; unchecked: number } {
  return {
    passed: part.checks.filter((c) => c.supported === true).length,
    unsupported: part.checks.filter((c) => c.supported === false).length,
    unchecked: part.checks.filter((c) => c.supported == null).length,
  };
}

/** One line on where a part has got to. */
export function summary(part: PartProgress): string {
  if (part.attempt > 0) {
    const { passed, unsupported } = checkCounts(part);
    const draft = part.attempt === 1 ? "First draft" : `Draft ${part.attempt}`;
    return `${draft}: ${passed + unsupported} of ${part.toCheck} points checked, ${unsupported} unsupported`;
  }
  if (part.requirements !== undefined) {
    return `${part.matched.length} of ${part.requirements} requirements matched`;
  }
  if (part.started) return part.model ? `Started with ${part.model}` : "Started";
  return "Waiting";
}

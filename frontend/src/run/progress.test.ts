import { describe, expect, it } from "vitest";
import type { ProgressEvent } from "../api/client";
import { addEvent, checkCounts, emptyProgress, summary } from "./progress";

const play = (events: ProgressEvent[]) => events.reduce(addEvent, emptyProgress());

// What the server always sends; each test gives only what it's about.
const at = { elapsed: 0 };

describe("progress", () => {
  it("puts the shared assessment's events under the assessment", () => {
    const p = play([
      { ...at, step: "started", model: "claude", shared_assessment: false },
      { ...at, step: "extracted", requirements: 2 },
      { step: "matched", requirement: "Terraform", kind: "essential", verdict: "met", elapsed: 4 },
    ]);
    expect(p.assessment.started).toBe(true);
    expect(p.assessment.matched.map((m) => m.requirement)).toEqual(["Terraform"]);
    expect(summary(p.assessment)).toBe("1 of 2 requirements matched");
    expect(p.cv.started).toBe(false);
  });

  it("keeps each document's drafting apart", () => {
    const p = play([
      { ...at, step: "drafted", artifact: "cv", attempt: 1, to_check: 3, dropped: 0 },
      { ...at, step: "checked", artifact: "cv", attempt: 1, point: "A", cv_ids: ["cv-001"], supported: true, problem: "" },
      { ...at, step: "checked", artifact: "cv", attempt: 1, point: "B", cv_ids: [], supported: false, problem: "no evidence" },
      { ...at, step: "drafted", artifact: "cover_letter_plan", attempt: 1, to_check: 5, dropped: 0 },
    ]);
    expect(checkCounts(p.cv)).toEqual({ passed: 1, unsupported: 1, unchecked: 0 });
    expect(summary(p.cv)).toBe("First draft: 2 of 3 points checked, 1 unsupported");
    expect(p.cover_letter_plan.checks).toEqual([]);
  });

  it("starts each redraft's checks afresh", () => {
    const p = play([
      { ...at, step: "drafted", artifact: "cv", attempt: 1, to_check: 2, dropped: 0 },
      { ...at, step: "checked", artifact: "cv", attempt: 1, point: "B", cv_ids: [], supported: false, problem: "vague" },
      { ...at, step: "drafted", artifact: "cv", attempt: 2, to_check: 1, dropped: 0 },
      { ...at, step: "checked", artifact: "cv", attempt: 2, point: "B2", cv_ids: ["cv-002"], supported: null, problem: "" },
    ]);
    expect(checkCounts(p.cv)).toEqual({ passed: 0, unsupported: 0, unchecked: 1 });
    expect(summary(p.cv)).toBe("Draft 2: 0 of 1 points checked, 0 unsupported");
  });

  it("leaves the progress it was given unchanged", () => {
    const before = emptyProgress();
    addEvent(before, { ...at, step: "extracted", requirements: 3 });
    expect(before.assessment.requirements).toBeUndefined();
  });
});

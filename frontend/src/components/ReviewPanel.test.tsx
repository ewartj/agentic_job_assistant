import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Review } from "../api/client";
import { ReviewPanel } from "./ReviewPanel";

// Shaped exactly as the API sends a review (keys included), so a renamed key shows up here.
const REVIEW: Review = {
  run_id: "acme_ds-20261117-101500",
  posting: "acme_ds",
  company: "Acme Health",
  role: "Data Scientist",
  assessment: "**Overall:** 13 met, 3 partly met, 1 not met of 17.",
  verdicts: { met: 13, "partly met": 3, "not met": 1 },
  round: 1,
  artifacts: [
    {
      kind: "cover_letter_plan", verified: true, points: 18, unsupported: 0, dropped: 0, drafts: 3,
      plan: "## Opening\n- I built the AWS platform [cv-001]", plan_path: "x.md", document_path: null, warnings: [],
    },
    {
      kind: "cv", verified: false, points: 21, unsupported: 2, dropped: 1, drafts: 3,
      plan: "## Profile\n- Engineer", plan_path: "y.md", document_path: "y.docx", warnings: ["acme: 1 bullet"],
    },
  ],
};

describe("ReviewPanel", () => {
  it("shows the verdict counts the API sends", () => {
    render(<ReviewPanel review={REVIEW} runId={REVIEW.run_id} />);
    expect(screen.getByText("13 met")).toBeInTheDocument();
    expect(screen.getByText("3 partly")).toBeInTheDocument();
    expect(screen.getByText("1 not met")).toBeInTheDocument();
  });

  it("shows each plan, whether it's supported, and its downloads", async () => {
    const user = userEvent.setup();
    render(<ReviewPanel review={REVIEW} runId={REVIEW.run_id} />);

    await user.click(screen.getByRole("tab", { name: /Letter plan/ }));
    expect(screen.getByText(/All 18 points are supported/)).toBeInTheDocument();
    expect(screen.queryByText(/Download the CV/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /CV/ }));
    expect(screen.getByText(/2 of 21 points aren't supported/)).toBeInTheDocument();
    expect(screen.getByText("acme: 1 bullet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Download the CV/ })).toHaveAttribute(
      "href",
      "/api/runs/acme_ds-20261117-101500/files/cv",
    );
  });

  it("links each tab to the panel it shows", () => {
    render(<ReviewPanel review={REVIEW} runId={REVIEW.run_id} />);
    const tab = screen.getByRole("tab", { name: "Assessment" });
    const panel = screen.getByRole("tabpanel");
    expect(tab).toHaveAttribute("aria-controls", panel.id);
    expect(panel).toHaveAccessibleName("Assessment");
  });

  it("moves between tabs with the arrow keys, Home and End", async () => {
    const user = userEvent.setup();
    render(<ReviewPanel review={REVIEW} runId={REVIEW.run_id} />);
    const tab = (name: RegExp) => screen.getByRole("tab", { name });

    await user.tab(); // Tab reaches the chosen tab only
    expect(tab(/Assessment/)).toHaveFocus();
    expect(tab(/Letter plan/)).toHaveAttribute("tabindex", "-1");

    await user.keyboard("{ArrowRight}");
    expect(tab(/Letter plan/)).toHaveFocus();
    expect(tab(/Letter plan/)).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("I built the AWS platform");

    await user.keyboard("{End}");
    expect(tab(/CV/)).toHaveFocus();
    await user.keyboard("{ArrowRight}"); // wraps round
    expect(tab(/Assessment/)).toHaveFocus();
    await user.keyboard("{ArrowLeft}");
    expect(tab(/CV/)).toHaveFocus();
    await user.keyboard("{Home}");
    expect(tab(/Assessment/)).toHaveAttribute("aria-selected", "true");
  });
});

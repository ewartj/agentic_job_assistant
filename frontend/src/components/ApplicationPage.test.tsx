import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ApplicationDetail } from "../api/client";
import { fails, fakeApi, ok } from "../test-utils";
import { ApplicationPage } from "./ApplicationPage";
import { ApplicationsPage } from "./ApplicationsPage";

afterEach(() => vi.unstubAllGlobals());

const APPLICATION: ApplicationDetail = {
  id: 7, company: "Acme Health", role: "Data Scientist", posting: "acme_ds", url: "https://example.com/job",
  status: "interested", applied_on: null, notes: null,
  created_at: "2026-11-19T10:00:00+00:00", updated_at: "2026-11-19T10:00:00+00:00",
  history: [{ status: "interested", note: "logged", changed_at: "2026-11-19T10:00:00+00:00" }],
  drafts: [
    { id: 1, kind: "assessment", version: 1, characters: 900, created_at: "2026-11-19T10:00:00+00:00" },
    { id: 2, kind: "cover_letter_plan", version: 1, characters: 1200, created_at: "2026-11-19T10:00:00+00:00" },
  ],
};
const APPLIED: ApplicationDetail = {
  ...APPLICATION, status: "applied", applied_on: "2026-11-21",
  history: [...APPLICATION.history, { status: "applied", note: "on their website", changed_at: "2026-11-21T09:00:00+00:00" }],
};

describe("ApplicationsPage", () => {
  it("lists the applications, filtered by status", async () => {
    fakeApi({ "GET /api/applications": [ok([APPLIED, { ...APPLICATION, id: 8, company: "Globex", role: "Engineer" }])] });
    const user = userEvent.setup();
    render(<ApplicationsPage />);

    expect(await screen.findByRole("link", { name: "Data Scientist at Acme Health" })).toHaveAttribute("href", "#/applications/7");
    await user.selectOptions(screen.getByLabelText("Show"), "Applied");
    expect(screen.queryByRole("link", { name: "Engineer at Globex" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Data Scientist at Acme Health" })).toBeInTheDocument();
  });
});

describe("applications you've sent", () => {
  it("are found whatever came of them, but not ones you only noted or withdrew unsent", async () => {
    const at = (id: number, status: ApplicationDetail["status"], applied_on: string | null = null) =>
      ({ ...APPLICATION, id, role: `Role ${id}`, status, applied_on });
    fakeApi({ "GET /api/applications": [ok([
      at(1, "interested"), at(2, "applied", "2026-11-01"), at(3, "interviewing"), at(4, "offer"),
      at(5, "rejected"), at(6, "withdrawn"), at(7, "withdrawn", "2026-10-01"),
    ])] });
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByRole("link", { name: "Role 1 at Acme Health" });

    await user.selectOptions(screen.getByLabelText("Show"), "Applied for (any outcome)");

    const shown = screen.getAllByRole("link").map((l) => l.textContent);
    expect(shown).toEqual(["Role 2 at Acme Health", "Role 3 at Acme Health", "Role 4 at Acme Health",
                           "Role 5 at Acme Health", "Role 7 at Acme Health"]);
  });
});

describe("ApplicationPage", () => {
  it("moves the application on, with a note, and shows it in the history", async () => {
    const fetch = fakeApi({ "GET /api/applications/7": [ok(APPLICATION)], "POST /api/applications/7/status": [ok(APPLIED)] });
    const user = userEvent.setup();
    render(<ApplicationPage applicationId={7} />);

    await user.selectOptions(await screen.findByLabelText("New status"), "Applied");
    await user.type(screen.getByLabelText(/Note/), "on their website");
    await user.click(screen.getByRole("button", { name: "Save" }));

    const history = within(screen.getByRole("heading", { name: "History" }).closest("section")!);
    expect(await history.findByText("on their website")).toBeInTheDocument();
    expect(screen.getByText("Applied 2026-11-21")).toBeInTheDocument();
    const post = fetch.mock.calls.map(([r]) => r).find((r) => r.method === "POST")!;
    expect(await post.json()).toEqual({ status: "applied", note: "on their website" });
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled(); // nothing to change now
  });

  it("says when a status change didn't go through", async () => {
    fakeApi({ "GET /api/applications/7": [ok(APPLICATION)], "POST /api/applications/7/status": [fails()] });
    const user = userEvent.setup();
    render(<ApplicationPage applicationId={7} />);

    await user.selectOptions(await screen.findByLabelText("New status"), "Applied");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("That didn't go through");
  });

  it("reads a draft", async () => {
    fakeApi({
      "GET /api/applications/7": [ok(APPLICATION)],
      "GET /api/applications/7/drafts/cover_letter_plan": [
        ok({ application_id: 7, kind: "cover_letter_plan", version: 1, text: "## Opening\n- I built it", created_at: "x" }),
      ],
    });
    const user = userEvent.setup();
    render(<ApplicationPage applicationId={7} />);

    const row = (await screen.findByText("Letter plan, version 1")).closest("li")!;
    await user.click(within(row).getByRole("button", { name: "Read" }));

    const draft = await screen.findByRole("article", { name: "Letter plan, version 1" });
    expect(within(draft).getByRole("heading", { name: "Opening" })).toBeInTheDocument();
  });

  it("says when there's no such application", async () => {
    fakeApi({ "GET /api/applications/7": [() => new Response(JSON.stringify({ detail: "No application with id 7" }), { status: 404 })] });
    render(<ApplicationPage applicationId={7} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("No application with id 7");
  });
});

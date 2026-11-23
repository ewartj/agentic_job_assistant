import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fakeApi, ok, unreachable } from "../test-utils";
import { Home } from "./Home";

afterEach(() => vi.unstubAllGlobals());

const RUNS = [
  { run_id: "globex_eng-20261120-090000", posting: "globex_eng", status: "awaiting_review" },
  { run_id: "acme_ds-20261119-101500", posting: "acme_ds", status: "saved" },
];
const POSTINGS = [
  { name: "acme_ds", company: "Acme Health", role: "Data Scientist", archived: false },
  { name: "globex_eng", company: "Globex", role: "Engineer", archived: false },
  { name: "initech_ml", company: "Initech", role: "ML Engineer", archived: false },
  { name: "umbrella_ds", company: "Umbrella", role: "Analyst", archived: true },
];

const section = (name: string) => screen.getByRole("heading", { name }).closest("section")!;

describe("Home", () => {
  it("shows the runs that need you, and only the jobs not yet assessed or archived", async () => {
    fakeApi({ "GET /api/runs": [ok(RUNS)], "GET /api/postings": [ok(POSTINGS)] });
    render(<Home />);

    expect(await screen.findByRole("link", { name: "globex_eng" })).toHaveAttribute("href", "#/runs/globex_eng-20261120-090000");
    expect(within(section("Needs you")).queryByText("acme_ds")).not.toBeInTheDocument(); // finished
    const fresh = within(section("New jobs"));
    expect(fresh.getByText("ML Engineer at Initech")).toBeInTheDocument();
    expect(fresh.queryByText("Data Scientist at Acme Health")).not.toBeInTheDocument(); // assessed
    expect(fresh.queryByText("Analyst at Umbrella")).not.toBeInTheDocument(); // archived
  });

  it("archives a job out of the new ones", async () => {
    const fetch = fakeApi({
      "GET /api/runs": [ok(RUNS)],
      "GET /api/postings": [ok(POSTINGS)],
      "POST /api/postings/initech_ml/archive": [ok({ ...POSTINGS[2], archived: true, text: "..." })],
    });
    const user = userEvent.setup();
    render(<Home />);

    await user.click(await within(section("New jobs")).findByRole("button", { name: "Archive" }));

    expect(await screen.findByText(/every saved job has been assessed or archived/)).toBeInTheDocument();
    const archive = fetch.mock.calls.map(([r]) => r).find((r) => r.method === "POST")!;
    expect(await archive.json()).toEqual({ archived: true });
  });

  it("says when job-web can't be reached, not 'Loading...' for ever, and can try again", async () => {
    fakeApi({ "GET /api/runs": [unreachable, ok(RUNS)], "GET /api/postings": [unreachable, ok(POSTINGS)] });
    const user = userEvent.setup();
    render(<Home />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't reach job-web");
    expect(screen.queryByText("Loading...")).not.toBeInTheDocument();
    expect(screen.getAllByText("Not loaded.")).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("link", { name: "globex_eng" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

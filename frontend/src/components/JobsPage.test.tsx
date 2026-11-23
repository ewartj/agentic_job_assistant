import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fakeApi, ok } from "../test-utils";
import { JobsPage } from "./JobsPage";

afterEach(() => vi.unstubAllGlobals());

const RUNS = [
  { run_id: "acme_ds-20261120-090000", posting: "acme_ds", status: "rejected" },
  { run_id: "acme_ds-20261119-101500", posting: "acme_ds", status: "saved" },
];
const POSTINGS = [
  { name: "acme_ds", company: "Acme Health", role: "Data Scientist", archived: false },
  { name: "initech_ml", company: "Initech", role: "ML Engineer", archived: false },
  { name: "umbrella_ds", company: "Umbrella", role: "Analyst", archived: true },
];

function setUp(extra: Record<string, Array<() => Response>> = {}) {
  return fakeApi({ "GET /api/runs": [ok(RUNS)], "GET /api/postings": [ok(POSTINGS)], ...extra });
}

const row = (text: string) => screen.getByText(text).closest("li")!;

describe("JobsPage", () => {
  it("shows how each job's latest run went, linking to it", async () => {
    setUp();
    render(<JobsPage />);

    const latest = await screen.findByRole("link", { name: "Latest run of Data Scientist at Acme Health" });
    expect(latest).toHaveAttribute("href", "#/runs/acme_ds-20261120-090000");
    expect(latest).toHaveTextContent("Rejected"); // the newest run, not the earlier saved one
    expect(within(row("ML Engineer at Initech")).getByText("Not assessed")).toBeInTheDocument();
  });

  it("hides archived jobs until asked, and can bring one back", async () => {
    setUp({ "POST /api/postings/umbrella_ds/archive": [ok({ ...POSTINGS[2], archived: false, text: "..." })] });
    const user = userEvent.setup();
    render(<JobsPage />);
    await screen.findByText("ML Engineer at Initech");
    expect(screen.queryByText("Analyst at Umbrella")).not.toBeInTheDocument();

    await user.click(screen.getByLabelText("Show archived (1)"));
    await user.click(within(row("Analyst at Umbrella")).getByRole("button", { name: "Bring back" }));

    expect(await screen.findByLabelText("Show archived (0)")).toBeInTheDocument();
    expect(within(row("Analyst at Umbrella")).getByRole("button", { name: "Archive" })).toBeInTheDocument();
  });

  it("opens a job's advert, fetching it only then", async () => {
    const fetch = setUp({ "GET /api/postings/initech_ml": [ok({ ...POSTINGS[1], text: "We need PyTorch." })] });
    const user = userEvent.setup();
    render(<JobsPage />);
    await screen.findByText("ML Engineer at Initech");
    expect(fetch).toHaveBeenCalledTimes(2); // the lists only

    await user.click(within(row("ML Engineer at Initech")).getByText("The advert"));

    expect(await screen.findByText("We need PyTorch.")).toBeInTheDocument();
  });
});

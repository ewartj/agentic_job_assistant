import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fakeApi, ok, unreachable } from "../test-utils";
import { Home } from "./Home";

afterEach(() => vi.unstubAllGlobals());

const RUNS = [{ run_id: "acme_ds-20261119-101500", posting: "acme_ds", status: "saved" }];
const POSTINGS = [{ name: "acme_ds", company: "Acme Health", role: "Data Scientist", saved: "2026-11-19" }];

describe("Home", () => {
  it("lists the runs and saved jobs", async () => {
    fakeApi({ "GET /api/runs": [ok(RUNS)], "GET /api/postings": [ok(POSTINGS)] });
    render(<Home />);

    expect(await screen.findByRole("link", { name: "acme_ds" })).toHaveAttribute("href", "#/runs/acme_ds-20261119-101500");
    expect(screen.getByText("Data Scientist at Acme Health")).toBeInTheDocument();
  });

  it("says when job-web can't be reached, not 'Loading...' for ever, and can try again", async () => {
    fakeApi({ "GET /api/runs": [unreachable, ok(RUNS)], "GET /api/postings": [unreachable, ok(POSTINGS)] });
    const user = userEvent.setup();
    render(<Home />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't reach job-web");
    expect(screen.queryByText("Loading...")).not.toBeInTheDocument();
    expect(screen.getAllByText("Not loaded.")).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("link", { name: "acme_ds" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

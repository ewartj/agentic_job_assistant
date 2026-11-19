import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Review, RunView } from "../api/client";
import { FakeEventSource, fails, fakeApi, ok } from "../test-utils";
import { RunPage } from "./RunPage";

const RUN = "acme_ds-20261119-101500";
const VIEW = `GET /api/runs/${RUN}`;

const REVIEW: Review = {
  run_id: RUN, posting: "acme_ds", company: "Acme Health", role: "Data Scientist",
  assessment: "**Overall:** 3 met of 3.", verdicts: { met: 3 }, round: 1, artifacts: [],
};

function view(status: RunView["status"], extra: Partial<RunView> = {}): RunView {
  return { run_id: RUN, posting: "acme_ds", status, ...extra };
}

/** The server sends a message on the run's events stream. */
function send(message: unknown) {
  act(() => FakeEventSource.last!.send(message));
}

beforeEach(() => FakeEventSource.install());
afterEach(() => vi.unstubAllGlobals());

describe("RunPage", () => {
  it("follows a working run's progress", async () => {
    fakeApi({ [VIEW]: [ok(view("running"))] });
    render(<RunPage runId={RUN} />);

    expect(await screen.findByRole("heading", { name: /Working/ })).toBeInTheDocument();
    expect(FakeEventSource.last!.url).toBe(`/api/runs/${RUN}/events`);
    send({ type: "progress", event: { step: "extracted", requirements: 7, elapsed: 3, artifact: null } });
    expect(await screen.findByText(/7 requirements/)).toBeInTheDocument();
  });

  it("shows how the run ended even if reloading it fails, and can try again", async () => {
    fakeApi({ [VIEW]: [ok(view("running")), fails(), ok(view("stopped", { error: "model went away" }))] });
    const user = userEvent.setup();
    render(<RunPage runId={RUN} />);
    await screen.findByRole("heading", { name: /Working/ });

    send({ type: "status", status: "stopped", error: "model went away" });

    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load the run");
    expect(screen.getByText("Stopped part-way: model went away")).toBeInTheDocument(); // not stuck on Working
    expect(screen.queryByRole("heading", { name: /Working/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("offers a decision only once the review has loaded", async () => {
    fakeApi({ [VIEW]: [ok(view("running")), fails(), ok(view("awaiting_review", { review: REVIEW }))] });
    const user = userEvent.setup();
    render(<RunPage runId={RUN} />);
    await screen.findByRole("heading", { name: /Working/ });

    send({ type: "status", status: "awaiting_review" });
    await screen.findByRole("alert");
    expect(screen.getByText("Ready for review")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve and save" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("button", { name: "Approve and save" })).toBeInTheDocument();
  });

  it("reconnects to a lost run when you try again", async () => {
    fakeApi({ [VIEW]: [ok(view("running"))] });
    const user = userEvent.setup();
    render(<RunPage runId={RUN} />);
    await screen.findByRole("heading", { name: /Working/ });
    const first = FakeEventSource.last!;

    act(() => first.fail(true));
    expect(await screen.findByRole("alert")).toHaveTextContent("Lost the connection");

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(FakeEventSource.last).not.toBe(first);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("says a failed decision didn't go through, and doesn't offer a reload in its place", async () => {
    fakeApi({
      [VIEW]: [ok(view("awaiting_review", { review: REVIEW }))],
      [`POST /api/runs/${RUN}/decision`]: [fails()],
    });
    const user = userEvent.setup();
    render(<RunPage runId={RUN} />);

    await user.click(await screen.findByRole("button", { name: "Approve and save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("That didn't go through");
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve and save" })).toBeEnabled(); // decide again
  });

  it("retries a stopped run from where it stopped", async () => {
    const fetch = fakeApi({
      [VIEW]: [ok(view("stopped", { error: "model went away" }))],
      [`POST /api/runs/${RUN}/retry`]: [ok(view("running"))],
    });
    const user = userEvent.setup();
    render(<RunPage runId={RUN} />);

    await user.click(await screen.findByRole("button", { name: "Retry" }));

    expect(fetch.mock.calls.some(([r]) => r.method === "POST")).toBe(true);
    expect(await screen.findByRole("heading", { name: /Working/ })).toBeInTheDocument();
    expect(FakeEventSource.last!.url).toBe(`/api/runs/${RUN}/events`); // following the retried work
  });
});

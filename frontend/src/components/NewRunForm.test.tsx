import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { NewRunForm } from "./NewRunForm";

function respond(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

async function fillIn(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Company"), "Acme");
  await user.type(screen.getByLabelText("Role"), "Data Scientist");
  await user.type(screen.getByLabelText("The advert"), "We need Python.");
}

afterEach(() => vi.unstubAllGlobals());

describe("NewRunForm", () => {
  it("saves the posting under a suggested name, then starts it", async () => {
    const fetch = vi.fn().mockResolvedValue(respond(201, { name: "acme_data_scientist", text: "We need Python." }));
    vi.stubGlobal("fetch", fetch);
    const onStart = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<NewRunForm onStart={onStart} />);

    await fillIn(user);
    expect(screen.getByLabelText("Save as")).toHaveValue("acme_data_scientist");
    await user.click(screen.getByRole("button", { name: "Save and assess" }));

    const request = fetch.mock.calls[0]![0] as Request;
    expect(request.method).toBe("POST");
    expect(new URL(request.url).pathname).toBe("/api/postings");
    expect(await request.json()).toMatchObject({ name: "acme_data_scientist", company: "Acme", replace: false });
    expect(onStart).toHaveBeenCalledWith("acme_data_scientist");
  });

  it("offers to replace a posting that's already saved", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(409, { detail: "acme_data_scientist.md already exists" })));
    const user = userEvent.setup();
    render(<NewRunForm onStart={vi.fn()} />);

    await fillIn(user);
    await user.click(screen.getByRole("button", { name: "Save and assess" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("already exists");
    expect(screen.getByLabelText(/Replace the saved posting/)).not.toBeChecked();
  });

  it("doesn't offer to replace the posting when it's the run that clashes", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(201, {})));
    const busy = new ApiError("acme_data_scientist is already being worked on", 409); // from starting the run
    const user = userEvent.setup();
    render(<NewRunForm onStart={vi.fn().mockRejectedValue(busy)} />);

    await fillIn(user);
    await user.click(screen.getByRole("button", { name: "Save and assess" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("already being worked on");
    expect(screen.queryByLabelText(/Replace the saved posting/)).not.toBeInTheDocument();
  });

  it("saves only once when just the start failed", async () => {
    const fetch = vi.fn().mockResolvedValue(respond(201, {}));
    vi.stubGlobal("fetch", fetch);
    const onStart = vi.fn().mockRejectedValueOnce(new Error("busy")).mockResolvedValueOnce(undefined);
    const user = userEvent.setup();
    render(<NewRunForm onStart={onStart} />);

    await fillIn(user);
    await user.click(screen.getByRole("button", { name: "Save and assess" }));
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "Save and assess" }));

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(onStart).toHaveBeenCalledTimes(2);
  });
});

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DecisionForm } from "./DecisionForm";

function setup() {
  const onDecide = vi.fn().mockResolvedValue(undefined);
  render(<DecisionForm onDecide={onDecide} busy={false} />);
  return { onDecide, user: userEvent.setup() };
}

describe("DecisionForm", () => {
  it("approves", async () => {
    const { onDecide, user } = setup();
    await user.click(screen.getByRole("button", { name: "Approve and save" }));
    expect(onDecide).toHaveBeenCalledWith({ action: "approve", notes: "", target: "both" });
  });

  it("only revises once you've said what to change", async () => {
    const { onDecide, user } = setup();
    const revise = screen.getByRole("button", { name: "Revise" });
    expect(revise).toBeDisabled();
    await user.type(screen.getByLabelText(/What to change/), "  Shorter opening ");
    await user.selectOptions(screen.getByRole("combobox"), "cv");
    await user.click(revise);
    expect(onDecide).toHaveBeenCalledWith({ action: "revise", notes: "Shorter opening", target: "cv" });
  });

  it("asks before rejecting", async () => {
    const { onDecide, user } = setup();
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(onDecide).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Yes, reject" }));
    expect(onDecide).toHaveBeenCalledWith(expect.objectContaining({ action: "reject" }));
  });
});

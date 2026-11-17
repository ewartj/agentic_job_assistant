import { describe, expect, it } from "vitest";
import { ApiError, errorMessage, unwrap } from "./client";

describe("errorMessage", () => {
  it("uses the API's own reason", () => {
    expect(errorMessage({ detail: "acme_ds is already being worked on" }, 409)).toBe(
      "acme_ds is already being worked on",
    );
  });

  it("joins validation messages, without Pydantic's prefix", () => {
    const body = { detail: [{ msg: "Value error, say what to change when revising", loc: ["body"] }] };
    expect(errorMessage(body, 422)).toBe("say what to change when revising");
  });

  it("falls back to the status code", () => {
    expect(errorMessage(undefined, 502)).toBe("the server answered 502");
  });
});

describe("unwrap", () => {
  it("returns the data, or throws an ApiError with the status", () => {
    expect(unwrap({ data: 1, response: new Response(null, { status: 200 }) })).toBe(1);
    expect(() => unwrap({ error: { detail: "no run" }, response: new Response(null, { status: 404 }) })).toThrow(
      new ApiError("no run", 404),
    );
  });
});

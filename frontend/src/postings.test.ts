import { describe, expect, it } from "vitest";
import { NAME, suggestName } from "./postings";
import { parseRoute, runHref } from "./routes";

describe("posting names", () => {
  it("are suggested from the company and role, by the API's rule", () => {
    const name = suggestName("Acme Health", "Senior Data Scientist (ML)");
    expect(name).toBe("acme_health_senior_data_scientist_ml");
    expect(NAME.test(name)).toBe(true);
  });

  it("are refused if the API would refuse them", () => {
    expect(NAME.test("../secrets")).toBe(false);
    expect(NAME.test("_hidden")).toBe(false);
  });
});

describe("routes", () => {
  it("round-trip a run id through the URL", () => {
    expect(parseRoute(runHref("acme_ds-20261116-101500"))).toEqual({ page: "run", runId: "acme_ds-20261116-101500" });
    expect(parseRoute("")).toEqual({ page: "home" });
    expect(parseRoute("#/nowhere")).toEqual({ page: "home" });
  });
});

import { describe, expect, it } from "vitest";
import type { PostingSummary, RunView } from "./api/client";
import { NAME, isNew, suggestName, withLatestRuns } from "./postings";
import { APPLICATIONS, JOBS, applicationHref, parseRoute, runHref } from "./routes";

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

  it("know the saved jobs and the tracker's pages", () => {
    expect(parseRoute(JOBS)).toEqual({ page: "jobs" });
    expect(parseRoute(APPLICATIONS)).toEqual({ page: "applications" });
    expect(parseRoute(applicationHref(12))).toEqual({ page: "application", applicationId: 12 });
    expect(parseRoute("#/applications/twelve")).toEqual({ page: "home" });
  });
});

describe("jobs", () => {
  const posting = (name: string, archived = false): PostingSummary => ({ name, archived });
  const run = (posting: string, status: RunView["status"], when: string): RunView => ({
    run_id: `${posting}-${when}`, posting, status,
  });

  it("each come with their latest run, from the newest-first list the API sends", () => {
    const runs = [run("acme", "saved", "20261120-1000"), run("acme", "rejected", "20261119-1000")];
    const [acme, globex] = withLatestRuns([posting("acme"), posting("globex")], runs);
    expect(acme!.latest?.status).toBe("saved");
    expect(globex!.latest).toBeNull();
  });

  it("are new until assessed or archived", () => {
    const jobs = withLatestRuns([posting("acme"), posting("globex"), posting("initech", true)],
                                [run("acme", "rejected", "20261120-1000")]);
    expect(jobs.filter(isNew).map((j) => j.name)).toEqual(["globex"]);
  });
});

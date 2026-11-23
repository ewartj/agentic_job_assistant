import type { PostingSummary, RunView } from "./api/client";

/** Posting names, by the same rule as the API: letters, digits, '_' and '-', starting with a letter or digit. */
export const NAME = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;

/** A name from the company and role, e.g. "Acme Health" + "Data Scientist" -> "acme_health_data_scientist". */
export function suggestName(company: string, role: string): string {
  return `${company} ${role}`
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 60);
}

/** A saved posting with its latest run, or null if it hasn't been assessed. */
export type Job = PostingSummary & { latest: RunView | null };

/** Each posting with its latest run. The API lists runs newest first, so the first one found is it. */
export function withLatestRuns(postings: PostingSummary[], runs: RunView[]): Job[] {
  const latest = new Map<string, RunView>();
  for (const run of runs) if (!latest.has(run.posting)) latest.set(run.posting, run);
  return postings.map((p) => ({ ...p, latest: latest.get(p.name) ?? null }));
}

/** Not assessed yet, and not put aside: what the home page offers. */
export function isNew(job: Job): boolean {
  return job.latest === null && !job.archived;
}

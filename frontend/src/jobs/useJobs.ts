/**
 * The saved jobs and the runs, loaded together (each job shows how its latest run went),
 * with the actions on a job: assess it, or archive it.
 */
import { useCallback, useEffect, useState } from "react";
import { api, unwrap, type RunView } from "../api/client";
import { withLatestRuns, type Job } from "../postings";
import { runHref } from "../routes";

export interface Jobs {
  /** null until loaded. */
  jobs: Job[] | null;
  runs: RunView[] | null;
  /** Why loading failed; reload() tries again. */
  error: string | null;
  loading: boolean;
  reload: () => void;
  /** Starts a run and opens its page. */
  start: (posting: string) => Promise<void>;
  setArchived: (posting: string, archived: boolean) => Promise<void>;
}

export function useJobs(): Jobs {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [runs, setRuns] = useState<RunView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // An earlier error stays on screen until this attempt ends, so a "Try again" button
  // (and keyboard focus on it) isn't pulled away mid-retry.
  const reload = useCallback(() => {
    setLoading(true);
    Promise.all([api.GET("/api/postings"), api.GET("/api/runs")])
      .then(([p, r]) => {
        const loadedRuns = unwrap(r);
        setJobs(withLatestRuns(unwrap(p), loadedRuns));
        setRuns(loadedRuns);
        setError(null);
      })
      .catch((e: unknown) =>
        setError(`Couldn't reach job-web (${e instanceof Error ? e.message : String(e)}). Is it running?`),
      )
      .finally(() => setLoading(false));
  }, []);

  useEffect(reload, [reload]);

  const start = useCallback(async (posting: string) => {
    const run = unwrap(await api.POST("/api/runs", { body: { posting } }));
    window.location.hash = runHref(run.run_id);
  }, []);

  const setArchived = useCallback(async (posting: string, archived: boolean) => {
    const saved = unwrap(
      await api.POST("/api/postings/{name}/archive", { params: { path: { name: posting } }, body: { archived } }),
    );
    setJobs((current) => current && current.map((j) => (j.name === posting ? { ...j, archived: saved.archived } : j)));
  }, []);

  return { jobs, runs, error, loading, reload, start, setArchived };
}

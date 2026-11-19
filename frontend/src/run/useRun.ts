/**
 * One run, kept up to date: its view from the API, its progress while it works, and the
 * actions on it. The API is the source of truth - after each stretch of work the view is
 * fetched again, rather than worked out here.
 */
import { useCallback, useEffect, useState } from "react";
import { api, unwrap, type Decision, type RunView } from "../api/client";
import { followRun } from "../api/events";
import { addEvent, emptyProgress, type Progress } from "./progress";

export interface RunState {
  view: RunView | null;
  progress: Progress;
  /** Why loading or an action failed (not why the run stopped: that's view.error). */
  error: string | null;
  busy: boolean;
  decide: (decision: Decision) => Promise<void>;
  retry: () => Promise<void>;
  /** Fetch the run again, and follow its progress again if it's still working. */
  refresh: () => void;
}

async function fetchRun(runId: string): Promise<RunView> {
  return unwrap(await api.GET("/api/runs/{run_id}", { params: { path: { run_id: runId } } }));
}

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function useRun(runId: string): RunState {
  const [view, setView] = useState<RunView | null>(null);
  const [progress, setProgress] = useState<Progress>(emptyProgress);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [connection, setConnection] = useState(0); // bumped to follow the events again

  const reload = useCallback(async () => {
    try {
      setView(await fetchRun(runId));
      setError(null);
    } catch (e) {
      setError(`Couldn't load the run (${message(e)}).`);
    }
  }, [runId]);

  useEffect(() => {
    setView(null);
    setProgress(emptyProgress());
    void reload();
  }, [reload]);

  // Follow the events whenever the run is working; stop when it pauses or ends.
  const running = view?.status === "running";
  useEffect(() => {
    if (!running) return;
    return followRun(runId, {
      onMessage: (m) => {
        if (m.type === "progress") setProgress((p) => addEvent(p, m.event));
        else if (m.type === "status") {
          // The status is known now, so the page never stays on "Working" - even if the
          // reload (for the review and the rest) fails. The review comes with the reload.
          setView((v) => v && { ...v, status: m.status, error: m.error ?? null });
          void reload();
        }
      },
      onLost: () => setError("Lost the connection to job-web. Is it still running?"),
    });
  }, [runId, running, reload, connection]);

  const refresh = useCallback(() => {
    setConnection((n) => n + 1);
    void reload();
  }, [reload]);

  const act = useCallback(async (request: () => Promise<RunView>) => {
    setBusy(true);
    try {
      const next = await request();
      setProgress(emptyProgress());
      setView(next);
      setError(null);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }, []);

  const path = { params: { path: { run_id: runId } } };
  const decide = (decision: Decision) =>
    act(async () => unwrap(await api.POST("/api/runs/{run_id}/decision", { ...path, body: decision })));
  const retry = () => act(async () => unwrap(await api.POST("/api/runs/{run_id}/retry", path)));

  return { view, progress, error, busy, decide, retry, refresh };
}

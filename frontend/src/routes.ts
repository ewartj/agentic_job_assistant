/**
 * The pages, kept in the URL's hash, so a reload or the back button keeps your place
 * without a router library:
 *
 *   #/                    home: a new job, and jobs not yet assessed
 *   #/runs/<id>           one run
 *   #/jobs                every saved job, with how its last run went
 *   #/applications        the tracker
 *   #/applications/<id>   one application
 */
import { useEffect, useState } from "react";

export type Route =
  | { page: "home" }
  | { page: "run"; runId: string }
  | { page: "jobs" }
  | { page: "applications" }
  | { page: "application"; applicationId: number };

export function parseRoute(hash: string): Route {
  const run = /^#\/runs\/([^/]+)$/.exec(hash);
  if (run?.[1]) return { page: "run", runId: decodeURIComponent(run[1]) };
  if (hash === "#/jobs") return { page: "jobs" };
  if (hash === "#/applications") return { page: "applications" };
  const application = /^#\/applications\/(\d+)$/.exec(hash);
  if (application?.[1]) return { page: "application", applicationId: Number(application[1]) };
  return { page: "home" };
}

export const HOME = "#/";
export const JOBS = "#/jobs";
export const APPLICATIONS = "#/applications";

export function runHref(runId: string): string {
  return `#/runs/${encodeURIComponent(runId)}`;
}

export function applicationHref(id: number): string {
  return `#/applications/${id}`;
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

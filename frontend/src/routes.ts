/**
 * The two pages, kept in the URL's hash (#/ and #/runs/<id>), so a reload or the back
 * button keeps your place without a router library.
 */
import { useEffect, useState } from "react";

export type Route = { page: "home" } | { page: "run"; runId: string };

export function parseRoute(hash: string): Route {
  const match = /^#\/runs\/([^/]+)$/.exec(hash);
  return match?.[1] ? { page: "run", runId: decodeURIComponent(match[1]) } : { page: "home" };
}

export function runHref(runId: string): string {
  return `#/runs/${encodeURIComponent(runId)}`;
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

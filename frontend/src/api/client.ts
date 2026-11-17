/**
 * The API client: typed from the API's own schema (openapi.json -> schema.d.ts), so a
 * route or model that changes on the server fails the type check here, not at run time.
 */
import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

type Schemas = components["schemas"];
export type Artifact = Schemas["Artifact"];
export type Decision = Schemas["Decision"];
export type NewPosting = Schemas["NewPosting"];
export type Posting = Schemas["Posting"];
export type PostingSummary = Schemas["PostingSummary"];
export type ProgressEvent = Schemas["ProgressMessage"]["event"];
export type Review = Schemas["Review"];
export type RunStatus = Schemas["RunView"]["status"];
export type RunView = Schemas["RunView"];
export type StreamMessage = Schemas["StreamMessage"];

/**
 * Same origin: Vite passes /api on to job-web in development. fetch is looked up on each
 * call (not once, here), so tests can stand in for it.
 */
export const api = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

/** A request the API refused, with its status code and the reason it gave. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * The reason in an error body. FastAPI sends {detail: "..."} for the API's own errors
 * (404, 409, 422) and {detail: [{msg, loc}, ...]} when a request fails validation.
 */
export function errorMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((d) => (d as { msg?: unknown }).msg)
      .filter((m): m is string => typeof m === "string")
      // Pydantic prefixes a custom validator's message with "Value error, ".
      .map((m) => m.replace(/^Value error, /, ""));
    if (messages.length) return messages.join("; ");
  }
  return `the server answered ${status}`;
}

/** The data from an openapi-fetch result, or an ApiError. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || result.data === undefined) {
    throw new ApiError(errorMessage(result.error, result.response.status), result.response.status);
  }
  return result.data;
}

/** Where to download one of a run's files. Only these kinds exist: never a path. */
export function fileUrl(runId: string, kind: "cover_letter_plan" | "cv_plan" | "cv"): string {
  return `/api/runs/${encodeURIComponent(runId)}/files/${kind}`;
}

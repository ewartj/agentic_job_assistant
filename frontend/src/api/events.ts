/**
 * Following a run's progress: server-sent events from /api/runs/{id}/events, one
 * StreamMessage per message - progress events while it works, then its status, then end.
 */
import type { StreamMessage } from "./client";

export interface Follow {
  onMessage: (message: StreamMessage) => void;
  /** The connection was lost and the browser gave up reconnecting. */
  onLost: () => void;
}

const isObject = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;

/** The message in an event's data, or null if it isn't one (so a bad line can't break the page). */
export function parseMessage(data: string): StreamMessage | null {
  let message: unknown;
  try {
    message = JSON.parse(data);
  } catch {
    return null;
  }
  if (!isObject(message)) return null;
  // Each kind with the fields the page relies on.
  const valid =
    message.type === "end" ||
    (message.type === "status" && typeof message.status === "string") ||
    (message.type === "progress" && isObject(message.event) && typeof message.event.step === "string");
  return valid ? (message as StreamMessage) : null;
}

/** Starts following; returns a function that stops. */
export function followRun(runId: string, { onMessage, onLost }: Follow): () => void {
  const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
  source.onmessage = (event: MessageEvent<string>) => {
    const message = parseMessage(event.data);
    if (message === null) {
      console.warn("Skipped a progress message that couldn't be read:", event.data);
      return;
    }
    if (message.type === "end") source.close(); // or the browser would reconnect
    onMessage(message);
  };
  // A dropped connection is retried by the browser itself (and the server then sends the
  // run's status), so only a connection the browser has given up on is reported.
  source.onerror = () => {
    if (source.readyState === EventSource.CLOSED) onLost();
  };
  return () => source.close();
}

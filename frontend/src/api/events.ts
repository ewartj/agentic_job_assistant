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

const TYPES: ReadonlySet<string> = new Set(["progress", "status", "end"]);

/** The message in an event's data, or null if it isn't one (so a bad line can't break the page). */
export function parseMessage(data: string): StreamMessage | null {
  try {
    const message: unknown = JSON.parse(data);
    const type = (message as { type?: unknown } | null)?.type;
    return typeof type === "string" && TYPES.has(type) ? (message as StreamMessage) : null;
  } catch {
    return null;
  }
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

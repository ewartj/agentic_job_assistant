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

/** Starts following; returns a function that stops. */
export function followRun(runId: string, { onMessage, onLost }: Follow): () => void {
  const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
  source.onmessage = (event: MessageEvent<string>) => {
    const message = JSON.parse(event.data) as StreamMessage;
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

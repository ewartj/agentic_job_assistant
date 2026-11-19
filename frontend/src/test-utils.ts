/** Stand-ins for the browser's network, shared by the tests. */
import { vi } from "vitest";

/** A JSON response, as the API sends. */
export function respond(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/**
 * Stands in for the API: replies by "METHOD /path", taking each route's replies in turn and
 * repeating the last. A request for any other route fails the test. Returns the fetch mock.
 */
export function fakeApi(routes: Record<string, Array<() => Response>>) {
  const fetch = vi.fn(async (request: Request) => {
    const route = `${request.method} ${new URL(request.url).pathname}`;
    const replies = routes[route];
    if (!replies?.length) throw new Error(`no reply set up for ${route}`);
    return (replies.length > 1 ? replies.shift()! : replies[0]!)();
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

/** The ways a reply can go. */
export const ok = (body: unknown) => () => respond(200, body);
export const fails = (status = 500) => () => respond(status, { detail: "Internal Server Error" });
export const unreachable = () => {
  throw new TypeError("Failed to fetch");
};

/**
 * An EventSource the test drives: install() puts it in place of the browser's, and
 * FakeEventSource.last is the one the code under test opened.
 */
export class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static last: FakeEventSource | null = null;

  readyState = FakeEventSource.OPEN;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(readonly url: string) {
    FakeEventSource.last = this;
  }

  static install(): void {
    FakeEventSource.last = null;
    vi.stubGlobal("EventSource", FakeEventSource);
  }

  close(): void {
    this.readyState = FakeEventSource.CLOSED;
  }

  /** The server sends a message (data is JSON-encoded unless it's already a string). */
  send(data: unknown): void {
    this.onmessage?.(new MessageEvent("message", { data: typeof data === "string" ? data : JSON.stringify(data) }));
  }

  /** The connection fails; the browser gives up if closed is true, else it will retry. */
  fail(closed: boolean): void {
    if (closed) this.readyState = FakeEventSource.CLOSED;
    this.onerror?.();
  }
}

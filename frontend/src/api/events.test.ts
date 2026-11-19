import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeEventSource } from "../test-utils";
import { followRun, parseMessage } from "./events";

beforeEach(() => FakeEventSource.install());
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks(); // console.warn, silenced in one test
});

describe("parseMessage", () => {
  it("reads the API's messages", () => {
    expect(parseMessage('{"type": "end"}')).toEqual({ type: "end" });
    expect(parseMessage('{"type": "status", "status": "saved"}')).toEqual({ type: "status", status: "saved" });
  });

  it.each([
    "not json", "", "null", "42", '{"no": "type"}', '{"type": "surprise"}',
    '{"type": "status"}', // no status
    '{"type": "progress"}', '{"type": "progress", "event": {}}', // no event, or one with no step
  ])(
    "turns %j into null, not an exception",
    (data) => expect(parseMessage(data)).toBeNull(),
  );
});

describe("followRun", () => {
  function follow() {
    const onMessage = vi.fn();
    const onLost = vi.fn();
    const stop = followRun("acme ds-1", { onMessage, onLost });
    return { onMessage, onLost, stop, source: FakeEventSource.last! };
  }

  it("follows the run's own events", () => {
    const { source } = follow();
    expect(source.url).toBe("/api/runs/acme%20ds-1/events");
  });

  it("skips a message it can't read and carries on", () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const { onMessage, source } = follow();
    source.send("{broken");
    source.send({ type: "status", status: "stopped" });
    expect(onMessage).toHaveBeenCalledTimes(1);
    expect(onMessage).toHaveBeenCalledWith({ type: "status", status: "stopped" });
  });

  it("closes at the end, so the browser doesn't reconnect", () => {
    const { onMessage, source } = follow();
    source.send({ type: "end" });
    expect(onMessage).toHaveBeenCalledWith({ type: "end" });
    expect(source.readyState).toBe(FakeEventSource.CLOSED);
  });

  it("reports a lost connection only once the browser gives up", () => {
    const { onLost, source } = follow();
    source.fail(false); // the browser will retry by itself
    expect(onLost).not.toHaveBeenCalled();
    source.fail(true);
    expect(onLost).toHaveBeenCalledOnce();
  });

  it("stops when asked", () => {
    const { stop, source } = follow();
    stop();
    expect(source.readyState).toBe(FakeEventSource.CLOSED);
  });
});

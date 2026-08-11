import { afterEach, expect, test, vi } from "vitest";

import { analyzeSession } from "./client";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

test("live analysis reports an eight-second event-stream stall", async () => {
  vi.useFakeTimers();
  let closeStream: (() => void) | undefined;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      closeStream = () => controller.close();
    },
  });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { status: 200 })));
  const onStall = vi.fn();

  const pending = analyzeSession(
    { sessionId: "session-test", token: "token-test" },
    () => undefined,
    { onStall },
  );
  await vi.advanceTimersByTimeAsync(7_999);
  expect(onStall).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1);
  expect(onStall).toHaveBeenCalledTimes(1);

  closeStream?.();
  await pending;
});

test("each server event resets the stall deadline", async () => {
  vi.useFakeTimers();
  let pushEvent: (() => void) | undefined;
  let closeStream: (() => void) | undefined;
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      pushEvent = () =>
        controller.enqueue(
          encoder.encode('event: stage_started\ndata: {"sequence":1,"state":"RETRIEVING"}\n\n'),
        );
      closeStream = () => controller.close();
    },
  });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { status: 200 })));
  const onStall = vi.fn();
  const onEvent = vi.fn();

  const pending = analyzeSession(
    { sessionId: "session-test", token: "token-test" },
    onEvent,
    { onStall },
  );
  await vi.advanceTimersByTimeAsync(7_000);
  pushEvent?.();
  await vi.advanceTimersByTimeAsync(0);
  expect(onEvent).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(7_999);
  expect(onStall).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1);
  expect(onStall).toHaveBeenCalledTimes(1);

  closeStream?.();
  await pending;
});

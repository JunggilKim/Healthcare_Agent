import { afterEach, expect, test, vi } from "vitest";

import { analyzeSession, createSession, replayProof, submitAnswer } from "./client";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

test("Korean display localization does not alter the analysis request contract", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ session_id: "session-s004", session_token: "token-s004" }), {
      status: 201,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await createSession({
    mode: "snapshot",
    seedCaseId: "S004",
    evaluationDate: "2026-08-11",
    language: "auto",
  });

  expect(fetchMock).toHaveBeenCalledWith("/api/v1/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode: "snapshot",
      seed_case_id: "S004",
      patient_text: null,
      evaluation_date: "2026-08-11",
      language: "auto",
      confirm_synthetic_public: false,
      identifier_warning_acknowledged: false,
    }),
  });
});

test("session creation translates rate limits into an actionable Korean retry message", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ code: "RATE_LIMITED", detail: "Too many requests" }),
        {
          status: 429,
          headers: { "Content-Type": "application/json", "Retry-After": "12" },
        },
      ),
    ),
  );

  await expect(
    createSession({
      mode: "live",
      seedCaseId: "S004",
      evaluationDate: "2026-08-28",
    }),
  ).rejects.toThrow("요청 한도를 초과했습니다. 12초 후 다시 시도해주세요.");
});

test("question answers preserve the exact English evidence payload", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response('event: completed\ndata: {"state":"complete"}\n\n', {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await submitAnswer(
    { sessionId: "session-s004", token: "token-s004" },
    "question-pathology",
    {
      answerText: "Existing pathology report confirms high-grade urothelial carcinoma.",
      idempotencyKey: "answer-turn-pathology",
    },
    () => undefined,
  );

  expect(fetchMock).toHaveBeenCalledWith("/api/v1/sessions/session-s004/answers", {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
      "Idempotency-Key": "answer-turn-pathology",
      "X-Session-Token": "token-s004",
    },
    body: JSON.stringify({
      question_id: "question-pathology",
      answer_text: "Existing pathology report confirms high-grade urothelial carcinoma.",
      structured_value: null,
      unknown: false,
      declined: false,
    }),
  });
});

test("structured question branches preserve their typed value", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response('event: completed\ndata: {"state":"QUESTION_READY"}\n\n', { status: 200 }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await submitAnswer(
    { sessionId: "session-s004", token: "token-s004" },
    "question-muscle-invasion",
    {
      structuredValue: { kind: "boolean", value: true },
      idempotencyKey: "answer-turn-muscle-invasion",
    },
    () => undefined,
  );

  expect(fetchMock).toHaveBeenCalledWith("/api/v1/sessions/session-s004/answers", {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
      "Idempotency-Key": "answer-turn-muscle-invasion",
      "X-Session-Token": "token-s004",
    },
    body: JSON.stringify({
      question_id: "question-muscle-invasion",
      answer_text: null,
      structured_value: { kind: "boolean", value: true },
      unknown: false,
      declined: false,
    }),
  });
});

test("proof replay trusts the server reexecution result instead of stored PV-012 flags", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          patient_state_version: 1,
          proof_packets: [{ verifier_checks: [{ check_id: "PV-012", passed: false }] }],
          replay_executed: true,
          replay_passed: true,
          replay_results: [{ passed: true }],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  );

  await expect(
    replayProof({ sessionId: "session-s004", token: "token-s004" }, "NCT05239624"),
  ).resolves.toEqual({
    passed: true,
    packetCount: 1,
    replayCount: 1,
    patientStateVersion: 1,
  });
});

test("SSE error events reject instead of looking like successful answers", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response('event: error\ndata: {"code":"SNAPSHOT_BRANCH_UNAVAILABLE"}\n\n', {
        status: 200,
      }),
    ),
  );

  await expect(
    submitAnswer(
      { sessionId: "session-s004", token: "token-s004" },
      "question-muscle-invasion",
      {
        structuredValue: { kind: "boolean", value: true },
        idempotencyKey: "answer-turn-error",
      },
      () => undefined,
    ),
  ).rejects.toThrow(
    "이 스냅샷 데모에는 선택한 답변 이후의 저장된 분석 경로가 없습니다.",
  );
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

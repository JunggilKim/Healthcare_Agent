import { sessionSchema, type SessionView } from "../types/api";

export interface SessionCredentials {
  sessionId: string;
  token: string;
}

interface StreamEvent {
  event: string;
  data: Record<string, unknown>;
}

async function jsonOrThrow(response: Response) {
  if (!response.ok) throw new Error(`API request failed: ${response.status}`);
  return (await response.json()) as unknown;
}

export async function createS004Session(): Promise<SessionCredentials> {
  const response = await fetch("/api/v1/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode: "snapshot",
      seed_case_id: "S004",
      evaluation_date: "2026-08-11",
      language: "en",
      confirm_synthetic_public: false,
      identifier_warning_acknowledged: false,
    }),
  });
  const payload = (await jsonOrThrow(response)) as { session_id: string; session_token: string };
  sessionStorage.setItem(`trial-opt:${payload.session_id}`, payload.session_token);
  return { sessionId: payload.session_id, token: payload.session_token };
}

export async function readSession(credentials: SessionCredentials): Promise<SessionView> {
  const response = await fetch(`/api/v1/sessions/${credentials.sessionId}`, {
    headers: { "X-Session-Token": credentials.token },
  });
  return sessionSchema.parse(await jsonOrThrow(response));
}

async function readEventStream(
  response: Response,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  if (!response.ok || !response.body) throw new Error(`Streaming request failed: ${response.status}`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const event = frame.match(/^event: (.+)$/m)?.[1];
      const data = frame.match(/^data: (.+)$/m)?.[1];
      if (event && data) onEvent({ event, data: JSON.parse(data) as Record<string, unknown> });
    }
    if (done) break;
  }
}

export async function analyzeSession(
  credentials: SessionCredentials,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const response = await fetch(`/api/v1/sessions/${credentials.sessionId}/analysis`, {
    method: "POST",
    headers: { Accept: "text/event-stream", "X-Session-Token": credentials.token },
  });
  await readEventStream(response, onEvent);
}

export async function submitPinnedAnswer(
  credentials: SessionCredentials,
  questionId: string,
  branch: "A" | "B",
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const answerText =
    branch === "A"
      ? "Existing pathology report confirms high-grade urothelial carcinoma."
      : "No pathology test has been performed; only the CT finding is available.";
  const response = await fetch(`/api/v1/sessions/${credentials.sessionId}/answers`, {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
      "X-Session-Token": credentials.token,
    },
    body: JSON.stringify({
      question_id: questionId,
      answer_text: answerText,
      structured_value: null,
      unknown: false,
      declined: false,
    }),
  });
  await readEventStream(response, onEvent);
}

export async function replayProof(credentials: SessionCredentials): Promise<boolean> {
  const response = await fetch(
    `/api/v1/sessions/${credentials.sessionId}/trials/NCT05239624/proof`,
    { headers: { "X-Session-Token": credentials.token } },
  );
  const payload = (await jsonOrThrow(response)) as {
    proof_packets: Array<{ verifier_checks: Array<{ check_id: string; passed: boolean }> }>;
  };
  return payload.proof_packets.every(
    (packet) => packet.verifier_checks.find((check) => check.check_id === "PV-012")?.passed,
  );
}


import {
  retrievalSchema,
  sessionSchema,
  type RetrievalView,
  type SessionView,
} from "../types/api";

export interface SessionCredentials {
  sessionId: string;
  token: string;
}

interface StreamEvent {
  event: string;
  data: Record<string, unknown>;
}

export interface DemoCase {
  id: string;
  text: string;
  has_full_snapshot: boolean;
}

async function jsonOrThrow(response: Response) {
  if (!response.ok) throw new Error(`API request failed: ${response.status}`);
  return (await response.json()) as unknown;
}

export interface CreateSessionInput {
  mode: "snapshot" | "live";
  seedCaseId?: string;
  patientText?: string;
  evaluationDate: string;
  language?: "ko" | "en" | "auto";
  confirmSyntheticPublic?: boolean;
  identifierWarningAcknowledged?: boolean;
}

export interface PublicConfig {
  supported_modes: string[];
  default_mode: string;
  live_available: boolean;
  snapshot_data_date: string;
  snapshot_version: string;
  disclaimer: string;
}

export async function readPublicConfig(): Promise<PublicConfig> {
  const response = await fetch("/api/v1/config/public");
  return (await jsonOrThrow(response)) as PublicConfig;
}

export async function createSession(input: CreateSessionInput): Promise<SessionCredentials> {
  const response = await fetch("/api/v1/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode: input.mode,
      seed_case_id: input.seedCaseId ?? null,
      patient_text: input.patientText ?? null,
      evaluation_date: input.evaluationDate,
      language: input.language ?? "auto",
      confirm_synthetic_public: input.confirmSyntheticPublic ?? false,
      identifier_warning_acknowledged: input.identifierWarningAcknowledged ?? false,
    }),
  });
  const payload = (await jsonOrThrow(response)) as { session_id: string; session_token: string };
  sessionStorage.setItem(`trial-opt:${payload.session_id}`, payload.session_token);
  return { sessionId: payload.session_id, token: payload.session_token };
}

export async function readDemoCases(): Promise<DemoCase[]> {
  const response = await fetch("/api/v1/demo/cases");
  const payload = (await jsonOrThrow(response)) as { cases: DemoCase[] };
  return payload.cases;
}

export async function readS004Retrieval(): Promise<RetrievalView> {
  const response = await fetch("/api/v1/demo/retrieval/S004");
  return retrievalSchema.parse(await jsonOrThrow(response));
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

export interface AnswerInput {
  answerText?: string;
  unknown?: boolean;
  declined?: boolean;
}

export async function submitAnswer(
  credentials: SessionCredentials,
  questionId: string,
  input: AnswerInput,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const response = await fetch(`/api/v1/sessions/${credentials.sessionId}/answers`, {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
      "X-Session-Token": credentials.token,
    },
    body: JSON.stringify({
      question_id: questionId,
      answer_text: input.answerText ?? null,
      structured_value: null,
      unknown: input.unknown ?? false,
      declined: input.declined ?? false,
    }),
  });
  await readEventStream(response, onEvent);
}

export async function replayProof(
  credentials: SessionCredentials,
  nctId: string,
): Promise<{ passed: boolean; packetCount: number }> {
  const response = await fetch(
    `/api/v1/sessions/${credentials.sessionId}/trials/${encodeURIComponent(nctId)}/proof`,
    { headers: { "X-Session-Token": credentials.token } },
  );
  const payload = (await jsonOrThrow(response)) as {
    proof_packets: Array<{ verifier_checks: Array<{ check_id: string; passed: boolean }> }>;
  };
  return {
    passed: payload.proof_packets.every(
    (packet) => packet.verifier_checks.find((check) => check.check_id === "PV-012")?.passed,
    ),
    packetCount: payload.proof_packets.length,
  };
}

export async function exportReport(credentials: SessionCredentials): Promise<void> {
  const response = await fetch(`/api/v1/sessions/${credentials.sessionId}/export`, {
    headers: { "X-Session-Token": credentials.token },
  });
  const payload = await jsonOrThrow(response);
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `trial-opt-${credentials.sessionId}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}

export async function resetSession(
  credentials: SessionCredentials,
): Promise<SessionCredentials> {
  const response = await fetch(`/api/v1/sessions/${credentials.sessionId}/reset`, {
    method: "POST",
    headers: { "X-Session-Token": credentials.token },
  });
  const payload = (await jsonOrThrow(response)) as {
    session_id: string;
    session_token: string;
  };
  sessionStorage.removeItem(`trial-opt:${credentials.sessionId}`);
  sessionStorage.setItem(`trial-opt:${payload.session_id}`, payload.session_token);
  return { sessionId: payload.session_id, token: payload.session_token };
}

export async function deleteSession(credentials: SessionCredentials): Promise<void> {
  const response = await fetch(`/api/v1/sessions/${credentials.sessionId}`, {
    method: "DELETE",
    headers: { "X-Session-Token": credentials.token },
  });
  await jsonOrThrow(response);
  sessionStorage.removeItem(`trial-opt:${credentials.sessionId}`);
}

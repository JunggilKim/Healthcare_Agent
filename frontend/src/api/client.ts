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

interface ProblemDetail {
  code?: string;
  detail?: string;
  title?: string;
}

const problemMessages: Record<string, string> = {
  SNAPSHOT_BRANCH_UNAVAILABLE: (
    "이 스냅샷 데모에는 선택한 답변 이후의 저장된 분석 경로가 없습니다. " +
    "새 스냅샷을 시작하거나 라이브 모드를 이용해주세요."
  ),
};

function problemMessage(code: unknown, fallback: string): string {
  return typeof code === "string" ? (problemMessages[code] ?? code) : fallback;
}

function codedError(code: unknown, fallback: string): Error {
  const error = new Error(problemMessage(code, fallback));
  if (typeof code === "string") error.name = code;
  return error;
}

async function responseError(response: Response, prefix: string): Promise<Error> {
  let problem: ProblemDetail | null = null;
  try {
    problem = (await response.clone().json()) as ProblemDetail;
  } catch {
    // Non-JSON gateway responses still retain their HTTP status below.
  }
  if (problem?.code === "RATE_LIMITED") {
    const retryAfter = response.headers.get("Retry-After");
    const suffix = retryAfter ? ` ${retryAfter}초 후 다시 시도해주세요.` : " 잠시 후 다시 시도해주세요.";
    return new Error(`요청 한도를 초과했습니다.${suffix}`);
  }
  if (problem?.code) {
    return codedError(problem.code, problem.detail ?? problem.title ?? prefix);
  }
  return new Error(problem?.detail ?? problem?.title ?? `${prefix}: ${response.status}`);
}

export interface DemoCase {
  id: string;
  text: string;
  has_full_snapshot: boolean;
  support_level: "full_evaluation" | "retrieval_only";
}

async function jsonOrThrow(response: Response) {
  if (!response.ok) throw await responseError(response, "API request failed");
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
  onStall?: () => void,
): Promise<void> {
  if (!response.ok) throw await responseError(response, "Streaming request failed");
  if (!response.body) throw new Error("Streaming response body is unavailable.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let stallTimer: ReturnType<typeof setTimeout> | undefined;
  const armStallTimer = () => {
    if (stallTimer) clearTimeout(stallTimer);
    stallTimer = setTimeout(() => onStall?.(), 8_000);
  };
  armStallTimer();
  try {
    for (;;) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        const event = frame.match(/^event: (.+)$/m)?.[1];
        const data = frame.match(/^data: (.+)$/m)?.[1];
        if (event && data) {
          armStallTimer();
          const payload = JSON.parse(data) as Record<string, unknown>;
          if (event === "error") {
            throw codedError(payload.code, "분석 스트림을 처리하지 못했습니다.");
          }
          onEvent({ event, data: payload });
        }
      }
      if (done) break;
    }
  } finally {
    if (stallTimer) clearTimeout(stallTimer);
  }
}

export async function analyzeSession(
  credentials: SessionCredentials,
  onEvent: (event: StreamEvent) => void,
  options: { onStall?: () => void; signal?: AbortSignal } = {},
): Promise<void> {
  const response = await fetch(`/api/v1/sessions/${credentials.sessionId}/analysis`, {
    method: "POST",
    headers: { Accept: "text/event-stream", "X-Session-Token": credentials.token },
    signal: options.signal,
  });
  await readEventStream(response, onEvent, options.onStall);
}

export interface AnswerInput {
  answerText?: string;
  structuredValue?: Record<string, unknown>;
  unknown?: boolean;
  declined?: boolean;
  idempotencyKey?: string;
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
      "Idempotency-Key": input.idempotencyKey ?? crypto.randomUUID(),
      "X-Session-Token": credentials.token,
    },
    body: JSON.stringify({
      question_id: questionId,
      answer_text: input.answerText ?? null,
      structured_value: input.structuredValue ?? null,
      unknown: input.unknown ?? false,
      declined: input.declined ?? false,
    }),
  });
  await readEventStream(response, onEvent);
}

export async function replayProof(
  credentials: SessionCredentials,
  nctId: string,
): Promise<{ passed: boolean; packetCount: number; replayCount: number; patientStateVersion: number }> {
  const response = await fetch(
    `/api/v1/sessions/${credentials.sessionId}/trials/${encodeURIComponent(nctId)}/proof`,
    { headers: { "X-Session-Token": credentials.token } },
  );
  const payload = (await jsonOrThrow(response)) as {
    patient_state_version: number;
    proof_packets: Array<unknown>;
    replay_executed: boolean;
    replay_passed: boolean;
    replay_results: Array<{ passed: boolean }>;
  };
  return {
    passed: payload.replay_executed && payload.replay_passed,
    packetCount: payload.proof_packets.length,
    replayCount: payload.replay_results.filter((result) => result.passed).length,
    patientStateVersion: payload.patient_state_version,
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

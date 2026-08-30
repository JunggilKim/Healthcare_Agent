import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";

import { sessionSchema } from "../types/api";
import { AgentTimeline } from "./AgentTimeline";
import { CriterionMatrix } from "./CriterionMatrix";
import { EvidenceFirewall } from "./EvidenceFirewall";
import { QuestionPanel } from "./QuestionPanel";
import { TrialCard } from "./TrialCard";

const session = sessionSchema.parse({
  session_id: "session-test",
  state: "QUESTION_READY",
  mode: "snapshot",
  evaluation_date: "2026-08-11",
  patient_text: "Synthetic patient text",
  patient_state_version: 0,
  facts: [],
  retrieval_hypotheses: [],
  criteria: [
    {
      criterion_id: "NCT05239624:INCLUSION:002:5f52ab88",
      source_direction: "INCLUSION",
      source_quote: "Histologically confirmed urothelial carcinoma",
      normalized_summary: "histology is urothelial carcinoma as stated by pathology",
      ast: { root_node_id: "node:0", nodes: [] },
    },
  ],
  proofs: [
    {
      criterion_id: "NCT05239624:INCLUSION:002:5f52ab88",
      criterion_source_hash: "a".repeat(64),
      final_verdict: "UNKNOWN",
      evidence_fact_ids: [],
      missing_slot_ids: ["pathology.histology"],
      hard_decision_allowed: false,
      derivation_steps: [],
      verifier_checks: [{ check_id: "PV-012", applicable: true, passed: true }],
    },
  ],
  trial_evaluation: {
    nct_id: "NCT05239624",
    decision: "POTENTIAL_MATCH",
    display_score: 47,
    proof_completeness: 1,
  },
  top_trial: {
    nct_id: "NCT05239624",
    title: "Snapshot trial",
    overall_status: "RECRUITING",
    data_timestamp: "2026-08-11T00:00:00Z",
  },
  current_question: {
    selected: {
      question_id: "q_00000000-0000-4000-8000-000000000001",
      slot_id: "pathology.histology",
      action: "REQUEST_RECORD",
      affected: [
        {
          criterion_id: "NCT05239624:INCLUSION:002:5f52ab88",
          nct_id: "NCT05239624",
        },
      ],
      utility_components: { mean_risk_reduction: 0.4, final_utility: 0.2 },
    },
    patient_facing_question: "병리검사 결과를 확인할 수 있나요?",
    deterministic_rationale: "미확인 기준에 영향을 줍니다.",
    top_alternatives: [],
  },
});

test("trial card labels the score as nonprobabilistic", () => {
  render(<TrialCard session={session} />);
  expect(screen.getByText(/근거 일치 점수 · 적합 확률이 아님/)).toBeVisible();
  expect(screen.getByText("POTENTIAL_MATCH")).toBeVisible();
});

test("review-required trials are not presented as a confirmed first-place result", () => {
  const reviewRequired = sessionSchema.parse({
    ...session,
    trial_evaluation: {
      ...session.trial_evaluation,
      decision: "REVIEW_REQUIRED",
      degradation_codes: ["PROTOCOL_COMPILATION_PARTIAL_COVERAGE"],
    },
  });

  render(<TrialCard session={reviewRequired} />);
  expect(screen.getByText("순위 보류")).toBeVisible();
  expect(screen.getByText("전문가 검토 대기")).toBeVisible();
  expect(screen.queryByText("1순위")).not.toBeInTheDocument();
});

test("retrieval-only cases never show an eligibility rank or score", () => {
  const retrievalOnly = sessionSchema.parse({
    ...session,
    support_level: "retrieval_only",
    trial_evaluation: null,
    top_trial: null,
    proofs: [],
    criteria: [],
  });

  render(<TrialCard session={retrievalOnly} />);
  expect(screen.getByText("검색 전용 사례")).toBeVisible();
  expect(screen.getByText("후보 순위와 적합 점수를 생성하지 않았습니다.")).toBeVisible();
  expect(screen.queryByText("1순위")).not.toBeInTheDocument();
});

test("retrieval-only evidence panels do not leak the S004 bladder-specific fixture", () => {
  const retrievalOnly = sessionSchema.parse({
    ...session,
    support_level: "retrieval_only",
    trial_evaluation: null,
    top_trial: null,
    proofs: [],
    criteria: [],
  });

  render(
    <>
      <EvidenceFirewall session={retrievalOnly} />
      <CriterionMatrix session={retrievalOnly} />
    </>,
  );
  expect(screen.getByText("검색 가설은 적격성 판정 근거가 아닙니다.")).toBeVisible();
  expect(screen.getByText("0개 선정 조건")).toBeVisible();
  expect(screen.getByText(/조건 구조화와 적격성 판정을 실행하지 않았습니다/)).toBeVisible();
  expect(screen.queryByText(/CT에서 방광 종괴/)).not.toBeInTheDocument();
});

test("criterion matrix exposes unresolved evidence and verifier state", () => {
  render(<CriterionMatrix session={session} />);
  expect(screen.getByText("병리검사로 요로상피암 조직형이 확인됨")).toBeVisible();
  expect(screen.getByText("병리 조직형")).toBeVisible();
  expect(screen.getByText("자동 검증 1/1 통과")).toBeVisible();
  fireEvent.click(screen.getByText("영어 원문 보기"));
  expect(screen.getByText("Pathology-confirmed urothelial histology")).toBeVisible();
});

test("evidence firewall follows the current histology proof state", () => {
  const { rerender } = render(<EvidenceFirewall session={session} />);
  expect(screen.getByText("Evidence Firewall · 근거 안전장치")).toBeVisible();
  expect(screen.getByText(/병리검사 결과가 없으므로/)).toBeVisible();
  expect(screen.getByText("CT 영상 소견")).toBeVisible();
  expect(screen.getByText("검색 단서로만 사용")).toBeVisible();
  expect(screen.getByText("병리 기록 없음")).toBeVisible();
  expect(screen.getByText("조직형 UNKNOWN 유지")).toBeVisible();

  const confirmed = sessionSchema.parse({
    ...session,
    patient_state_version: 1,
    facts: [
      {
        fact_id: "fact-pathology",
        slot_id: "pathology.histology",
        grade: "A",
      },
    ],
    proofs: session.proofs.map((proof) => ({
      ...proof,
      final_verdict: "PASS",
      evidence_fact_ids: ["fact-pathology"],
      missing_slot_ids: [],
    })),
  });
  rerender(<EvidenceFirewall session={confirmed} />);

  expect(screen.getByText(/확인된 병리 근거만 조직형 판정에 반영합니다/)).toBeVisible();
  expect(screen.getByText(/근육 침윤처럼 확인되지 않은 조건은 UNKNOWN/)).toBeVisible();
  expect(screen.getByText("Grade A 병리 근거")).toBeVisible();
  expect(screen.getByText("조직형 PASS 허용")).toBeVisible();
  expect(screen.queryByText(/병리검사 결과가 없으므로/)).not.toBeInTheDocument();
});

test("question panel always offers unknown and record-decline controls", () => {
  render(<QuestionPanel session={session} busy={false} onAnswer={() => undefined} />);
  expect(screen.getByRole("button", { name: "기록 내용이 불명확함" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "기록을 확인할 수 없음" })).toBeEnabled();
  expect(screen.getByText(/새 검사를 권하는 항목이 아닙니다/)).toBeVisible();
});

test("question value buttons submit the backend typed value instead of the display label", () => {
  const onAnswer = vi.fn();
  const booleanSession = sessionSchema.parse({
    ...session,
    current_question: {
      ...session.current_question,
      selected: {
        ...session.current_question?.selected,
        slot_id: "pathology.muscle_invasion",
        branches: [
          {
            branch_id: "q:boolean:0",
            label: "true",
            response_kind: "VALUE",
            synthetic_value: { kind: "boolean", value: true },
          },
          {
            branch_id: "q:boolean:1",
            label: "false",
            response_kind: "VALUE",
            synthetic_value: { kind: "boolean", value: false },
          },
        ],
      },
    },
  });

  render(<QuestionPanel session={booleanSession} busy={false} onAnswer={onAnswer} />);
  fireEvent.click(screen.getByRole("button", { name: "기록에서 확인됨" }));
  expect(onAnswer).toHaveBeenCalledWith({
    structuredValue: { kind: "boolean", value: true },
  });
});

test("patient questions use slot-specific natural Korean choices", () => {
  const consentSession = sessionSchema.parse({
    ...session,
    mode: "live",
    current_question: {
      ...session.current_question,
      selected: {
        ...session.current_question?.selected,
        slot_id: "consent.informed_provided",
        action: "ASK_PATIENT",
        branches: [
          {
            branch_id: "q:consent:0",
            label: "true",
            response_kind: "VALUE",
            synthetic_value: { kind: "boolean", value: true },
          },
          {
            branch_id: "q:consent:1",
            label: "false",
            response_kind: "VALUE",
            synthetic_value: { kind: "boolean", value: false },
          },
        ],
      },
    },
  });

  render(<QuestionPanel session={consentSession} busy={false} onAnswer={() => undefined} />);
  expect(screen.getByRole("button", { name: "예, 동의했습니다" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "아니요, 동의하지 않았습니다" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "잘 모르겠습니다" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "답변하지 않겠습니다" })).toBeEnabled();
});

test("live histology questions use their server-provided typed branches", () => {
  const onAnswer = vi.fn();
  const liveSession = sessionSchema.parse({
    ...session,
    mode: "live",
    current_question: {
      ...session.current_question,
      selected: {
        ...session.current_question?.selected,
        branches: [
          {
            branch_id: "q:histology:0",
            label: "adenocarcinoma",
            response_kind: "VALUE",
            synthetic_value: {
              kind: "categorical",
              system: "trial-opt-canonical-v1",
              value: "adenocarcinoma",
            },
          },
        ],
      },
    },
  });

  render(<QuestionPanel session={liveSession} busy={false} onAnswer={onAnswer} />);
  expect(screen.queryByText("병리기록에서 고등급 요로상피암 확인")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "adenocarcinoma" }));
  expect(onAnswer).toHaveBeenCalledWith({
    structuredValue: {
      kind: "categorical",
      system: "trial-opt-canonical-v1",
      value: "adenocarcinoma",
    },
  });
});

test("limited sessions do not claim that every evidence check is complete", () => {
  const limited = sessionSchema.parse({
    ...session,
    degradation_codes: ["PROTOCOL_COMPILATION_PARTIAL_COVERAGE"],
    current_question: {
      selected: null,
      stop_reason: "UTILITY_BELOW_THRESHOLD",
      top_alternatives: [],
      patient_facing_question: null,
      deterministic_rationale: "추가 질문의 예상 효용이 중단 기준보다 낮습니다.",
    },
  });

  render(<QuestionPanel session={limited} busy={false} onAnswer={() => undefined} />);
  expect(screen.getByText("현재 제한적 결과에서 선택된 다음 질문이 없습니다.")).toBeVisible();
  expect(screen.getByText("추가 질문의 예상 효용이 중단 기준보다 낮습니다.")).toBeVisible();
  expect(screen.queryByText(/근거 평가를 모두 마쳤습니다/)).not.toBeInTheDocument();
});

test("protocol degradation explains why no safe next question was generated", () => {
  const limited = sessionSchema.parse({
    ...session,
    degradation_codes: ["PROTOCOL_COMPILATION_PARTIAL_COVERAGE"],
    current_question: {
      selected: null,
      stop_reason: "PROTOCOL_REVIEW_REQUIRED",
      top_alternatives: [],
      patient_facing_question: null,
      deterministic_rationale: "상위 후보의 임상시험 조건 구조화 또는 검토가 완료되지 않았습니다.",
    },
  });

  render(<QuestionPanel session={limited} busy={false} onAnswer={() => undefined} />);
  expect(screen.getByText("프로토콜 검토 전에는 다음 질문을 생성하지 않습니다.")).toBeVisible();
  expect(screen.getByText(/조건 구조화 또는 검토/)).toBeVisible();
});

test("snapshot branch exhaustion is presented as a completed frozen demo path", () => {
  const completed = sessionSchema.parse({
    ...session,
    state: "COMPLETE",
    current_question: {
      selected: null,
      stop_reason: "SNAPSHOT_BRANCH_COVERAGE_EXHAUSTED",
      top_alternatives: [],
      patient_facing_question: null,
      deterministic_rationale: (
        "이 스냅샷 데모에서 준비된 답변 경로를 모두 확인했습니다. " +
        "더 많은 질문을 이어가려면 라이브 모드로 새 분석을 시작하세요."
      ),
    },
  });

  render(<QuestionPanel session={completed} busy={false} onAnswer={() => undefined} />);
  expect(screen.getByText("스냅샷 데모의 준비된 질문을 모두 확인했습니다.")).toBeVisible();
  expect(screen.getByText(/라이브 모드로 새 분석/)).toBeVisible();
});

test("agent timeline communicates degraded state in text", () => {
  render(
    <AgentTimeline
      states={{
        "Patient Evidence": "completed",
        "Trial Retrieval": "degraded",
      }}
    />,
  );
  expect(screen.getByText("대체 경로 사용")).toBeVisible();
  expect(screen.getByText("완료")).toBeVisible();
});

test("agent timeline marks non-executed retrieval-only stages as out of scope", () => {
  render(
    <AgentTimeline
      states={{
        "Patient Evidence": "completed",
        "Trial Retrieval": "completed",
        "Protocol Compilation": "skipped",
      }}
    />,
  );
  expect(screen.getByText("검색 전용 범위 밖")).toBeVisible();
});

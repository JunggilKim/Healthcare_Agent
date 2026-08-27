import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";

import { sessionSchema } from "../types/api";
import { AgentTimeline } from "./AgentTimeline";
import { CriterionMatrix } from "./CriterionMatrix";
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

test("criterion matrix exposes unresolved evidence and verifier state", () => {
  render(<CriterionMatrix session={session} />);
  expect(screen.getByText("병리검사로 요로상피암 조직형이 확인됨")).toBeVisible();
  expect(screen.getByText("병리 조직형")).toBeVisible();
  expect(screen.getByText("자동 검증 1/1 통과")).toBeVisible();
  fireEvent.click(screen.getByText("영어 원문 보기"));
  expect(screen.getByText("Pathology-confirmed urothelial histology")).toBeVisible();
});

test("question panel always offers unknown and record-decline controls", () => {
  render(<QuestionPanel session={session} busy={false} onAnswer={() => undefined} />);
  expect(screen.getByRole("button", { name: "현재 기록으로는 모르겠어요" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "이 기록을 확인할 수 없어요" })).toBeEnabled();
  expect(screen.getByText(/새 검사를 권하는 질문이 아닙니다/)).toBeVisible();
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
  fireEvent.click(screen.getByRole("button", { name: "기존 기록에서 확인됨" }));
  expect(onAnswer).toHaveBeenCalledWith({
    structuredValue: { kind: "boolean", value: true },
  });
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

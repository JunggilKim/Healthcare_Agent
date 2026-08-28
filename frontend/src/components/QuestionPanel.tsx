import {
  localizedQuestionValue,
  questionActionLabels,
  questionFallbackLabels,
  questionNotice,
} from "../lib/locale";
import type { SessionView } from "../types/api";

interface Props {
  session: SessionView;
  busy: boolean;
  onAnswer: (answer: {
    answerText?: string;
    structuredValue?: Record<string, unknown>;
    unknown?: boolean;
    declined?: boolean;
  }) => void;
}

export function QuestionPanel({ session, busy, onAnswer }: Props) {
  const selection = session.current_question;
  const question = selection?.selected;
  if (!selection || !question) {
    const degraded = session.degradation_codes.length > 0;
    const snapshotComplete =
      selection?.stop_reason === "SNAPSHOT_BRANCH_COVERAGE_EXHAUSTED";
    const protocolReview = selection?.stop_reason === "PROTOCOL_REVIEW_REQUIRED";
    const retrievalOnly = selection?.stop_reason === "RETRIEVAL_ONLY_CASE";
    return (
      <section className="panel empty-question">
        <strong>{retrievalOnly ? "이 사례는 임상시험 검색 결과만 제공합니다." : protocolReview ? "프로토콜 검토 전에는 다음 질문을 생성하지 않습니다." : snapshotComplete ? "스냅샷 데모의 준비된 질문을 모두 확인했습니다." : degraded ? "현재 제한적 결과에서 선택된 다음 질문이 없습니다." : "현재 기록으로 선택된 다음 질문이 없습니다."}</strong>
        {selection?.deterministic_rationale ? <p className="mt-2">{selection.deterministic_rationale}</p> : null}
      </section>
    );
  }
  const isPinnedS004Histology =
    session.mode === "snapshot" &&
    session.top_trial?.nct_id === "NCT05239624" &&
    question.slot_id === "pathology.histology";
  const fallbackLabels = questionFallbackLabels(question.action);
  return (
    <section className="panel question-panel min-h-0 flex-1 overflow-y-auto" aria-labelledby="question-title">
      <div className="question-topline"><p className="eyebrow">NEXT QUESTION · 다음 확인 항목</p><span className="mode-badge" title={question.action}>{questionActionLabels[question.action] ?? question.action}</span></div>
      <h2 id="question-title">{selection.patient_facing_question}</h2>
      <p className="question-rationale">{selection.deterministic_rationale}</p>
      <p className="record-notice">{questionNotice(question.action)}</p>
      {isPinnedS004Histology ? (
        <div className="mt-2 grid grid-cols-2 gap-2">
          <button aria-label="Pinned branch A · 병리기록에서 고등급 요로상피암 확인" className="primary-button px-3 py-2 text-xs" disabled={busy} onClick={() => onAnswer({ answerText: "Existing pathology report confirms high-grade urothelial carcinoma." })}><strong>A</strong><span>병리기록에서 고등급 요로상피암 확인</span></button>
          <button aria-label="Pinned branch B · 병리검사 미시행 / CT만 있음" className="secondary-button px-3 py-2 text-xs" disabled={busy} onClick={() => onAnswer({ answerText: "No pathology test has been performed; only the CT finding is available." })}><strong>B</strong><span>병리검사 미시행 · CT 소견만 있음</span></button>
        </div>
      ) : (
        <div className="mt-2 grid grid-cols-2 gap-2">
          {question.branches.filter((branch) => branch.response_kind === "VALUE").map((branch) => <button key={branch.branch_id} className="secondary-button px-3 py-2 text-xs" disabled={busy} onClick={() => onAnswer({ structuredValue: branch.synthetic_value ?? undefined })}>{localizedQuestionValue(branch.label, { action: question.action, slotId: question.slot_id })}</button>)}
        </div>
      )}
      <div className="mt-2 grid grid-cols-2 gap-2">
        <button className="secondary-button px-3 py-2 text-xs" disabled={busy} onClick={() => onAnswer({ unknown: true })}>{fallbackLabels.unknown}</button>
        <button className="secondary-button px-3 py-2 text-xs" disabled={busy} onClick={() => onAnswer({ declined: true })}>{fallbackLabels.declined}</button>
      </div>
    </section>
  );
}

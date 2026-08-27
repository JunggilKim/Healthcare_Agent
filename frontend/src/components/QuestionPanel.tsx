import { localizedQuestionValue, questionActionLabels } from "../lib/locale";
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
  if (!selection || !question) return <section className="panel empty-question">현재 기록으로 가능한 근거 평가를 모두 마쳤습니다. 추가로 확인할 항목이 없습니다.</section>;
  const isHistology = question.slot_id === "pathology.histology";
  return (
    <section className="panel question-panel min-h-0 flex-1 overflow-y-auto" aria-labelledby="question-title">
      <div className="question-topline"><p className="eyebrow">NEXT QUESTION · 다음 확인 항목</p><span className="mode-badge" title={question.action}>{questionActionLabels[question.action] ?? question.action}</span></div>
      <h2 id="question-title">{selection.patient_facing_question}</h2>
      <p className="question-rationale">{selection.deterministic_rationale}</p>
      <p className="record-notice"><strong>새 검사를 권하는 질문이 아닙니다.</strong> 이미 보유한 기록에서 확인 가능한 내용만 묻습니다.</p>
      {isHistology ? (
        <div className="mt-2 grid grid-cols-2 gap-2">
          <button aria-label="Pinned branch A · 병리기록에서 고등급 요로상피암 확인" className="primary-button px-3 py-2 text-xs" disabled={busy} onClick={() => onAnswer({ answerText: "Existing pathology report confirms high-grade urothelial carcinoma." })}><strong>A</strong><span>병리기록에서 고등급 요로상피암 확인</span></button>
          <button aria-label="Pinned branch B · 병리검사 미시행 / CT만 있음" className="secondary-button px-3 py-2 text-xs" disabled={busy} onClick={() => onAnswer({ answerText: "No pathology test has been performed; only the CT finding is available." })}><strong>B</strong><span>병리검사 미시행 · CT 소견만 있음</span></button>
        </div>
      ) : (
        <div className="mt-2 grid grid-cols-2 gap-2">
          {question.branches.filter((branch) => branch.response_kind === "VALUE").map((branch) => <button key={branch.branch_id} className="secondary-button px-3 py-2 text-xs" disabled={busy} onClick={() => onAnswer({ structuredValue: branch.synthetic_value ?? undefined })}>{localizedQuestionValue(branch.label)}</button>)}
        </div>
      )}
      <div className="mt-2 grid grid-cols-2 gap-2">
        <button aria-label="현재 기록으로는 모르겠어요" className="secondary-button px-3 py-2 text-xs" disabled={busy} onClick={() => onAnswer({ unknown: true })}>현재 기록에 정보 없음</button>
        <button aria-label="이 기록을 확인할 수 없어요" className="secondary-button px-3 py-2 text-xs" disabled={busy} onClick={() => onAnswer({ declined: true })}>기록 확인 불가</button>
      </div>
    </section>
  );
}

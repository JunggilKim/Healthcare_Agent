import type { SessionView } from "../types/api";

interface Props {
  session: SessionView;
  busy: boolean;
  onAnswer: (answer: { answerText?: string; unknown?: boolean; declined?: boolean }) => void;
}

export function QuestionPanel({ session, busy, onAnswer }: Props) {
  const selection = session.current_question;
  const question = selection?.selected;
  if (!selection || !question) return <section className="panel empty-question">추가 질문 없이 현재 근거를 보고합니다 · No further question</section>;
  const isHistology = question.slot_id === "pathology.histology";
  return (
    <section className="panel question-panel min-h-0 flex-1 overflow-y-auto" aria-labelledby="question-title">
      <div className="question-topline"><p className="eyebrow">다음 확인 질문 · NEXT QUESTION</p><span className="mode-badge">{question.action}</span></div>
      <h2 id="question-title">{selection.patient_facing_question}</h2>
      <p className="question-rationale">{selection.deterministic_rationale}</p>
      <p className="record-notice">기존 기록 확인 — 새 검사를 권하지 않습니다. (Existing record confirmation — no new test is being recommended.)</p>
      {isHistology ? (
        <div className="mt-2 grid grid-cols-2 gap-2">
          <button aria-label="Pinned branch A · 병리기록에서 고등급 요로상피암 확인" className="primary-button px-3 py-2 text-xs" disabled={busy} onClick={() => onAnswer({ answerText: "Existing pathology report confirms high-grade urothelial carcinoma." })}>A · 고등급 요로상피암 확인됨</button>
          <button aria-label="Pinned branch B · 병리검사 미시행 / CT만 있음" className="secondary-button px-3 py-2 text-xs" disabled={busy} onClick={() => onAnswer({ answerText: "No pathology test has been performed; only the CT finding is available." })}>B · 병리 미시행 / CT만 있음</button>
        </div>
      ) : (
        <div className="mt-2 grid grid-cols-2 gap-2">
          {question.branches.filter((branch) => branch.response_kind === "VALUE").map((branch) => <button key={branch.branch_id} className="secondary-button px-3 py-2 text-xs" disabled={busy} onClick={() => onAnswer({ answerText: branch.label })}>{branch.label}</button>)}
        </div>
      )}
      <div className="mt-2 grid grid-cols-2 gap-2">
        <button className="secondary-button px-3 py-2 text-xs" disabled={busy} onClick={() => onAnswer({ unknown: true })}>잘 모르겠습니다</button>
        <button className="secondary-button px-3 py-2 text-xs" disabled={busy} onClick={() => onAnswer({ declined: true })}>이 기록을 제공할 수 없습니다</button>
      </div>
    </section>
  );
}

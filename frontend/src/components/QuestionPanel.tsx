import type { SessionView } from "../types/api";

interface Props {
  session: SessionView;
  busy: boolean;
  onAnswer: (branch: "A" | "B") => void;
}

export function QuestionPanel({ session, busy, onAnswer }: Props) {
  const selection = session.current_question;
  const question = selection?.selected;
  if (!selection || !question) return <section className="panel">추가 질문 없이 현재 근거를 보고합니다.</section>;
  const isHistology = question.slot_id === "pathology.histology";
  return (
    <section className="panel" aria-labelledby="question-title">
      <div className="flex items-center justify-between gap-3"><p className="eyebrow">TRIAL-OPT NEXT ACTION</p><span className="mode-badge">{question.action}</span></div>
      <h2 id="question-title" className="mt-4 text-xl font-bold leading-relaxed">{selection.patient_facing_question}</h2>
      <p className="mt-3 text-sm leading-6 text-slate-400">{selection.deterministic_rationale}</p>
      <p className="mt-3 rounded-xl bg-cyan-300/10 p-3 text-xs text-cyan-100">Existing record confirmation — no new test is being recommended.</p>
      {isHistology ? (
        <div className="mt-5 grid gap-3">
          <button className="primary-button" disabled={busy} onClick={() => onAnswer("A")}>Pinned branch A · 병리기록에서 고등급 요로상피암 확인</button>
          <button className="secondary-button" disabled={busy} onClick={() => onAnswer("B")}>Pinned branch B · 병리검사 미시행 / CT만 있음</button>
        </div>
      ) : (
        <p className="mt-5 rounded-xl border border-amber-300/30 p-4 text-sm text-amber-100">Vertical slice의 두 번째 정확한 행동입니다. 병리 답변이 근육 침윤을 추론하지 않았습니다.</p>
      )}
    </section>
  );
}


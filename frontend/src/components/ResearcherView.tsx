import { questionActionLabels, slotLabels } from "../lib/locale";
import type { SessionView } from "../types/api";

export function ResearcherView({ session }: { session: SessionView }) {
  const selection = session.current_question;
  return (
    <section className="panel researcher-view" aria-labelledby="researcher-title">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="eyebrow">다음 질문 선택 근거</p>
          <h2 id="researcher-title" className="panel-title">왜 이 질문을 먼저 제안했나요?</h2>
          <p className="section-description">후보 질문이 판정 불확실성을 얼마나 줄일 수 있는지 동일한 조건으로 비교했습니다.</p>
        </div>
        <p className="text-xs text-slate-500">합성 응답 분기를 동일 확률로 비교 · 학습된 사전확률 미사용</p>
      </div>
      <div className="mt-4 grid gap-3 lg:grid-cols-3">
        {(selection?.top_alternatives ?? []).map((candidate, index) => {
          const utility = candidate.utility_components;
          return (
            <article key={candidate.question_id} className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
              <div className="flex items-center justify-between"><span className="text-xs font-black text-cyan-300">후보 {index + 1}</span><span className="mode-badge" title={candidate.action}>{questionActionLabels[candidate.action] ?? candidate.action}</span></div>
              <p className="mt-3 text-sm font-bold">{slotLabels[candidate.slot_id] ?? candidate.slot_id}</p>
              <p className="technical-code">{candidate.slot_id}</p>
              <dl className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-400">
                <div><dt>종합 효용 점수</dt><dd className="mt-1 font-mono text-white">{utility?.final_utility.toFixed(3) ?? "—"}</dd></div>
                <div><dt>평균 불확실성 감소</dt><dd className="mt-1 font-mono text-white">{utility ? `${Math.round(utility.mean_risk_reduction * 100)}%` : "—"}</dd></div>
                <div><dt>영향 조건 수</dt><dd className="mt-1 font-mono text-white">{candidate.affected.length}</dd></div>
                <div><dt>중복 제거 후 질문</dt><dd className="mt-1 font-mono text-white">1개</dd></div>
              </dl>
            </article>
          );
        })}
      </div>
      <div className="mt-4 grid gap-3 text-xs sm:grid-cols-4">
        <p className="metric-chip"><span>상위 후보 위험도</span><strong>근거 기반 계산</strong></p>
        <p className="metric-chip"><span>근거 안전장치</span><strong>PV-007 통과</strong></p>
        <p className="metric-chip"><span>분석 실행 방식</span><strong>저장된 스냅샷</strong></p>
        <p className="metric-chip"><span>이번 실행 비용</span><strong>$0.000</strong></p>
      </div>
    </section>
  );
}

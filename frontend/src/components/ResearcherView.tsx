import type { SessionView } from "../types/api";

export function ResearcherView({ session }: { session: SessionView }) {
  const selection = session.current_question;
  return (
    <section className="panel researcher-view" aria-labelledby="researcher-title">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="eyebrow">연구자 보기 · RESEARCHER VIEW</p>
          <h2 id="researcher-title" className="panel-title">질문 효용 감사 · Question utility audit</h2>
        </div>
        <p className="text-xs text-slate-500">균등 합성 분기 · Uniform synthetic branches · learned prior 없음</p>
      </div>
      <div className="mt-4 grid gap-3 lg:grid-cols-3">
        {(selection?.top_alternatives ?? []).map((candidate, index) => {
          const utility = candidate.utility_components;
          return (
            <article key={candidate.question_id} className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
              <div className="flex items-center justify-between"><span className="text-xs font-black text-cyan-300">#{index + 1}</span><span className="mode-badge">{candidate.action}</span></div>
              <p className="mt-3 break-all text-sm font-bold">{candidate.slot_id}</p>
              <dl className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-400">
                <div><dt>최종 효용 · Final utility</dt><dd className="mt-1 font-mono text-white">{utility?.final_utility.toFixed(3) ?? "—"}</dd></div>
                <div><dt>평균 위험 감소</dt><dd className="mt-1 font-mono text-white">{utility ? `${Math.round(utility.mean_risk_reduction * 100)}%` : "—"}</dd></div>
                <div><dt>영향 조건 수</dt><dd className="mt-1 font-mono text-white">{candidate.affected.length}</dd></div>
                <div><dt>Slot 중복 제거</dt><dd className="mt-1 font-mono text-white">1 action</dd></div>
              </dl>
            </article>
          );
        })}
      </div>
      <div className="mt-4 grid gap-3 text-xs sm:grid-cols-4">
        <p className="metric-chip"><span>Current top-K risk</span><strong>Proof-derived</strong></p>
        <p className="metric-chip"><span>Evidence firewall</span><strong>PV-007 clear</strong></p>
        <p className="metric-chip"><span>Models</span><strong>Snapshot cache</strong></p>
        <p className="metric-chip"><span>Estimated cost</span><strong>$0.000 live</strong></p>
      </div>
    </section>
  );
}

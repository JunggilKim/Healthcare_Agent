import type { SessionView } from "../types/api";

export function TrialCard({ session }: { session: SessionView }) {
  const trial = session.trial_evaluation;
  if (!trial) return <section className="panel">임상시험 근거를 계산하고 있습니다…</section>;
  const counts = session.proofs.reduce<Record<string, number>>((acc, proof) => {
    acc[proof.final_verdict] = (acc[proof.final_verdict] ?? 0) + 1;
    return acc;
  }, {});
  return (
    <article className="panel border-cyan-400/30">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rank-badge">#1</span>
        <span className="mode-badge">RECRUITING · PINNED 2026-08-11</span>
      </div>
      <p className="mt-5 text-sm font-semibold text-cyan-300">NCT05239624</p>
      <h2 className="mt-2 text-2xl font-bold leading-snug">Enfortumab Vedotin and Pembrolizumab in People With Bladder Cancer</h2>
      <div className="mt-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-wider text-slate-500">현재 사전 선별 상태</p>
          <p className="mt-1 text-lg font-bold text-amber-200">{trial.decision}</p>
        </div>
        <div className="text-right">
          <p className="text-3xl font-black text-white">{trial.display_score}</p>
          <p className="text-xs text-slate-400">evidence match score · 확률 아님</p>
        </div>
      </div>
      <div className="mt-5 grid grid-cols-5 gap-2 text-center text-xs">
        {(["PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE", "CONFLICT"] as const).map((verdict) => (
          <div key={verdict} className="rounded-xl bg-slate-950/70 p-2">
            <strong className="block text-base text-white">{counts[verdict] ?? 0}</strong>
            <span className="text-slate-500">{verdict === "NOT_APPLICABLE" ? "N/A" : verdict}</span>
          </div>
        ))}
      </div>
      <p className="mt-4 text-sm text-slate-400">Proof completeness {Math.round(trial.proof_completeness * 100)}%</p>
    </article>
  );
}


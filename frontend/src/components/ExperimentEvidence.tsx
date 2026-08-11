import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export function ExperimentEvidence() {
  return (
    <section className="panel" aria-labelledby="experiment-title">
      <p className="eyebrow">EXPERIMENT EVIDENCE</p>
      <h2 id="experiment-title" className="panel-title">Committed evaluation artifact</h2>
      <div className="mt-4 grid gap-5 lg:grid-cols-[1fr_0.8fr]">
        <div className="h-64 rounded-xl border border-dashed border-slate-700 bg-slate-950/50 p-4">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={[]}>
              <CartesianGrid stroke="#1e293b" />
              <XAxis dataKey="questions" stroke="#64748b" />
              <YAxis stroke="#64748b" />
              <Tooltip />
              <Line dataKey="accuracy" stroke="#67e8f9" />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="flex flex-col justify-center rounded-xl border border-amber-300/30 bg-amber-100/5 p-5">
          <p className="font-bold text-amber-200">Phase 6 metrics not generated yet</p>
          <p className="mt-2 text-sm leading-6 text-slate-400">브라우저에서 연구 수치를 계산하거나 임의 값을 표시하지 않습니다. 고정 seed 평가 JSON이 생성되면 이 셸이 그대로 읽습니다.</p>
        </div>
      </div>
    </section>
  );
}

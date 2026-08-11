import { useQuery } from "@tanstack/react-query";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { z } from "zod";

const pointSchema = z.object({ questions: z.number(), accuracy: z.number() });
const completedPolicySchema = z.object({
  policy: z.string(),
  runs: z.number(),
  final_decision_accuracy: z.number(),
  question_count_mean: z.number(),
  accuracy_auc: z.number(),
  median_questions_to_stable_top3: z.number(),
});
const pendingPolicySchema = z.object({
  policy: z.literal("B5"),
  status: z.string(),
});
const policySchema = z.union([completedPolicySchema, pendingPolicySchema]);
const ablationSchema = z.object({
  ablation: z.string(),
  final_decision_accuracy: z.number(),
  question_count_mean: z.number(),
  safety_metric_status: z.string().optional(),
});
const summarySchema = z.object({
  claim_scope: z.string(),
  acceptance_eligible: z.boolean(),
  clinical_validation: z.boolean(),
  blocking_reasons: z.array(z.string()),
  run_ids: z.record(z.string(), z.string()),
  metrics: z.object({
    criterion_macro_f1_self_consistency: z.number(),
    unsupported_hard_decision_rate_fixture: z.number(),
    false_pre_screen_pass_rate_fixture: z.number(),
    retrieval_recall_at_20_proxy: z.number(),
    b6_final_decision_accuracy_fixture: z.number(),
  }),
  accuracy_curves: z.object({
    B0: z.array(pointSchema),
    B3: z.array(pointSchema),
    B6: z.array(pointSchema),
  }),
  policy_table: z.array(policySchema),
  ablation_table: z.array(ablationSchema),
});

async function readSummary() {
  const response = await fetch("/eval/summary.json");
  if (!response.ok) throw new Error("evaluation artifact unavailable");
  return summarySchema.parse(await response.json());
}

export function ExperimentEvidence() {
  const query = useQuery({ queryKey: ["evaluation-summary"], queryFn: readSummary });
  if (query.isPending) return <section className="panel">Evaluation artifact loading…</section>;
  if (query.isError) {
    return <section className="panel text-amber-100">Evaluation artifact unavailable · no metric is imputed.</section>;
  }
  const summary = query.data;
  const curve = summary.accuracy_curves.B0.map((point, index) => ({
    questions: point.questions,
    B0: point.accuracy,
    B3: summary.accuracy_curves.B3[index]?.accuracy,
    B6: summary.accuracy_curves.B6[index]?.accuracy,
  }));
  return (
    <section className="panel" aria-labelledby="experiment-title">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="eyebrow">EXPERIMENT EVIDENCE</p>
          <h2 id="experiment-title" className="panel-title">Committed evaluation artifact</h2>
        </div>
        <span className="mode-badge">FIXED SEED · 20260811</span>
      </div>
      <div className="mt-4 rounded-xl border border-amber-300/40 bg-amber-100/5 p-4 text-sm text-amber-100">
        <p className="font-bold">Provisional fixture smoke · release acceptance evidence 아님</p>
        <p className="mt-1 text-xs leading-5">{summary.claim_scope}. Clinical validation: {String(summary.clinical_validation)}. Acceptance eligible: {String(summary.acceptance_eligible)}.</p>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-slate-300">{summary.blocking_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
      </div>
      <div className="mt-4 grid gap-5 xl:grid-cols-[1fr_0.85fr]">
        <div className="h-72 rounded-xl border border-slate-800 bg-slate-950/50 p-4" aria-label="Accuracy versus questions chart">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={curve}>
              <CartesianGrid stroke="#1e293b" />
              <XAxis dataKey="questions" stroke="#94a3b8" />
              <YAxis domain={[0, 1]} stroke="#94a3b8" />
              <Tooltip />
              <Legend />
              <Line dataKey="B0" stroke="#94a3b8" strokeWidth={2} />
              <Line dataKey="B3" stroke="#fbbf24" strokeWidth={2} />
              <Line dataKey="B6" stroke="#67e8f9" strokeWidth={3} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="grid grid-cols-2 gap-3 text-xs">
          <p className="metric-chip"><span>Criterion Macro-F1</span><strong>{summary.metrics.criterion_macro_f1_self_consistency.toFixed(3)}</strong><small>AST self-consistency only</small></p>
          <p className="metric-chip"><span>Unsupported hard decisions</span><strong>{(summary.metrics.unsupported_hard_decision_rate_fixture * 100).toFixed(1)}%</strong><small>S004 fixture only</small></p>
          <p className="metric-chip"><span>Retrieval Recall@20</span><strong>{summary.metrics.retrieval_recall_at_20_proxy.toFixed(3)}</strong><small>exact-condition proxy qrels</small></p>
          <p className="metric-chip"><span>B6 final accuracy</span><strong>{summary.metrics.b6_final_decision_accuracy_fixture.toFixed(3)}</strong><small>single-trial smoke only</small></p>
          <p className="metric-chip"><span>False pre-screen pass</span><strong>{(summary.metrics.false_pre_screen_pass_rate_fixture * 100).toFixed(1)}%</strong><small>generated fixture only</small></p>
        </div>
      </div>
      <div className="mt-4 overflow-x-auto" tabIndex={0} aria-label="Question policy smoke table">
        <table className="w-full min-w-[680px] text-left text-xs">
          <thead className="text-slate-400"><tr><th className="pb-2">Policy</th><th>Runs</th><th>Final accuracy</th><th>Accuracy AUC</th><th>Mean questions</th><th>Median to stable top-3*</th></tr></thead>
          <tbody className="divide-y divide-slate-800">{summary.policy_table.map((policy) => <tr key={policy.policy}><td className="py-2 font-bold text-cyan-200">{policy.policy}</td>{"status" in policy ? <td colSpan={5} className="py-2 text-amber-200">{policy.status}</td> : <><td>{policy.runs}</td><td>{policy.final_decision_accuracy.toFixed(3)}</td><td>{policy.accuracy_auc.toFixed(3)}</td><td>{policy.question_count_mean.toFixed(2)}</td><td>{policy.median_questions_to_stable_top3.toFixed(1)}</td></>}</tr>)}</tbody>
        </table>
      </div>
      <p className="mt-2 text-[0.65rem] text-slate-400">* Single-trial fixture proxy; stable top-3 is not estimable and this value is not an acceptance result.</p>
      <div className="mt-4 overflow-x-auto" tabIndex={0} aria-label="Selected ablation smoke table">
        <table className="w-full min-w-[680px] text-left text-xs">
          <thead className="text-slate-400"><tr><th className="pb-2">Ablation</th><th>Final accuracy</th><th>Mean questions</th><th>Safety metric status</th></tr></thead>
          <tbody className="divide-y divide-slate-800">{summary.ablation_table.map((item) => <tr key={item.ablation}><td className="py-2 font-bold text-fuchsia-200">{item.ablation}</td><td>{item.final_decision_accuracy.toFixed(3)}</td><td>{item.question_count_mean.toFixed(2)}</td><td>{item.safety_metric_status ?? "fixture run"}</td></tr>)}</tbody>
        </table>
      </div>
      <p className="mt-4 break-all font-mono text-[0.65rem] text-slate-400">Runs · {Object.values(summary.run_ids).join(" · ")}</p>
    </section>
  );
}

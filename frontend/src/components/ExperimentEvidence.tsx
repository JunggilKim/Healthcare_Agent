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
  if (query.isPending) return <section className="panel runtime-loading">실험 평가 결과를 불러오고 있습니다…</section>;
  if (query.isError) {
    return <section className="panel runtime-error">실험 평가 결과를 불러오지 못했습니다. 사용할 수 없는 지표를 임의 값으로 채우지 않습니다.</section>;
  }
  const summary = query.data;
  const curve = summary.accuracy_curves.B0.map((point, index) => ({
    questions: point.questions,
    B0: point.accuracy,
    B3: summary.accuracy_curves.B3[index]?.accuracy,
    B6: summary.accuracy_curves.B6[index]?.accuracy,
  }));
  return (
    <section className="panel experiment-evidence" aria-labelledby="experiment-title">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="eyebrow">실험 평가 결과</p>
          <h2 id="experiment-title" className="panel-title">질문 전략과 안전장치 비교</h2>
          <p className="section-description">저장소에 포함된 고정 시드 평가 결과를 그대로 시각화합니다.</p>
        </div>
        <span className="mode-badge">고정 시드 · 2026.08.11</span>
      </div>
      <div className="experiment-warning">
        <p className="font-bold">해석 범위에 주의하세요.</p>
        <p className="mt-1 text-xs leading-5">이 결과는 발표용 고정 데이터에서 실행한 기능 점검입니다. 임상적 성능 검증이나 출시 승인 근거가 아닙니다.</p>
        <details className="source-original"><summary>평가 범위 원문 보기</summary><div className="source-original-content"><p>{summary.claim_scope}</p><p>Clinical validation: {String(summary.clinical_validation)} · Acceptance eligible: {String(summary.acceptance_eligible)}</p><ul>{summary.blocking_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></div></details>
      </div>
      <div className="mt-4 grid gap-5 xl:grid-cols-[1fr_0.85fr]">
        <div className="experiment-chart" aria-label="질문 수에 따른 판정 정확도 변화">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={curve}>
              <CartesianGrid stroke="#d9e2ec" />
              <XAxis dataKey="questions" stroke="#64748b" />
              <YAxis domain={[0, 1]} stroke="#64748b" />
              <Tooltip />
              <Legend />
              <Line dataKey="B0" stroke="#64748b" strokeWidth={2} />
              <Line dataKey="B3" stroke="#b45309" strokeWidth={2} />
              <Line dataKey="B6" stroke="#2563eb" strokeWidth={3} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="grid grid-cols-2 gap-3 text-xs">
          <p className="metric-chip"><span>조건 판정 일관성</span><strong>{summary.metrics.criterion_macro_f1_self_consistency.toFixed(3)}</strong><small>구조화 규칙 내부 일관성</small></p>
          <p className="metric-chip"><span>근거 없는 확정 판정</span><strong>{(summary.metrics.unsupported_hard_decision_rate_fixture * 100).toFixed(1)}%</strong><small>S004 고정 사례 기준</small></p>
          <p className="metric-chip"><span>검색 재현율@20</span><strong>{summary.metrics.retrieval_recall_at_20_proxy.toFixed(3)}</strong><small>대리 정답 집합 기준</small></p>
          <p className="metric-chip"><span>B6 최종 정확도</span><strong>{summary.metrics.b6_final_decision_accuracy_fixture.toFixed(3)}</strong><small>단일 임상시험 기능 점검</small></p>
          <p className="metric-chip"><span>잘못된 사전 선별 통과</span><strong>{(summary.metrics.false_pre_screen_pass_rate_fixture * 100).toFixed(1)}%</strong><small>생성된 고정 사례 기준</small></p>
        </div>
      </div>
      <div className="mt-4 overflow-x-auto" tabIndex={0} aria-label="Question policy smoke table">
        <table className="w-full min-w-[680px] text-left text-xs">
          <thead className="text-slate-400"><tr><th className="pb-2">질문 전략</th><th>실행 횟수</th><th>최종 정확도</th><th>정확도 AUC</th><th>평균 질문 수</th><th>상위 3개 안정화 중앙값*</th></tr></thead>
          <tbody className="divide-y divide-slate-800">{summary.policy_table.map((policy) => <tr key={policy.policy}><td className="py-2 font-bold text-cyan-200">{policy.policy}</td>{"status" in policy ? <td colSpan={5} className="py-2 text-amber-200">{policy.status}</td> : <><td>{policy.runs}</td><td>{policy.final_decision_accuracy.toFixed(3)}</td><td>{policy.accuracy_auc.toFixed(3)}</td><td>{policy.question_count_mean.toFixed(2)}</td><td>{policy.median_questions_to_stable_top3.toFixed(1)}</td></>}</tr>)}</tbody>
        </table>
      </div>
      <p className="mt-2 text-[0.65rem] text-slate-400">* 단일 임상시험 고정 사례에서는 상위 3개 안정화를 추정할 수 없습니다. 이 값은 출시 승인 지표가 아닙니다.</p>
      <div className="mt-4 overflow-x-auto" tabIndex={0} aria-label="Selected ablation smoke table">
        <table className="w-full min-w-[680px] text-left text-xs">
          <thead className="text-slate-400"><tr><th className="pb-2">제거 실험</th><th>최종 정확도</th><th>평균 질문 수</th><th>안전 지표 상태</th></tr></thead>
          <tbody className="divide-y divide-slate-800">{summary.ablation_table.map((item) => <tr key={item.ablation}><td className="py-2 font-bold text-fuchsia-200">{item.ablation}</td><td>{item.final_decision_accuracy.toFixed(3)}</td><td>{item.question_count_mean.toFixed(2)}</td><td>{item.safety_metric_status ?? "fixture run"}</td></tr>)}</tbody>
        </table>
      </div>
      <details className="source-original mt-4"><summary>실행 ID 보기</summary><div className="source-original-content break-all font-mono">{Object.values(summary.run_ids).join(" · ")}</div></details>
    </section>
  );
}

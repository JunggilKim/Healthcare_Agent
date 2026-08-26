import type { SessionView } from "../types/api";
import { verdictLabels } from "../lib/locale";
import { StatusBadge } from "./ClinicalUI";

export function TrialCard({ session }: { session: SessionView }) {
  const trial = session.trial_evaluation;
  const top = session.top_trial;
  if (!trial) return <section className="panel">임상시험 근거를 계산하고 있습니다…</section>;
  const counts = session.proofs.reduce<Record<string, number>>((acc, proof) => {
    acc[proof.final_verdict] = (acc[proof.final_verdict] ?? 0) + 1;
    return acc;
  }, {});
  return (
    <article className="panel trial-card">
      <div className="trial-card-topline">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rank-badge" aria-label="trial rank 1">1</span>
          <span className="mode-badge">순위 변화 · RANK Δ 0</span>
          <span className="mode-badge">{top?.nct_id === "NCT05239624" ? "모집 중 · RECRUITING · PINNED 2026-08-11" : `${top?.overall_status ?? "STATUS UNKNOWN"} · ${top?.data_timestamp ? `DATA ${top.data_timestamp.slice(0, 10)}` : "PINNED SNAPSHOT"}`}</span>
        </div>
        <StatusBadge code={trial.decision} />
      </div>
      <p className="trial-id">{top?.nct_id ?? trial.nct_id}</p>
      <h2 className="trial-title">{top?.title ?? trial.nct_id}</h2>
      <p className="source-preserved">공식 임상시험 제목 · Official registry title</p>
      <div className="trial-summary">
        <div>
          <p className="summary-label">현재 사전 선별 상태</p>
          <p className="summary-value">{verdictLabels[trial.decision] ?? trial.decision}</p>
        </div>
        <div className="text-right">
          <p className="score-value">{trial.display_score}</p>
          <p className="score-label">evidence match score · 확률 아님</p>
        </div>
      </div>
      <div className="verdict-metrics">
        {(["PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE", "CONFLICT"] as const).map((verdict) => (
          <div key={verdict} className={`verdict-metric verdict-metric-${verdict.toLowerCase()}`}>
            <strong>{counts[verdict] ?? 0}</strong>
            <span>{verdict === "NOT_APPLICABLE" ? "N/A" : verdict}</span>
          </div>
        ))}
      </div>
      <div className="trial-card-footer">
        <span>Proof 완성도 {Math.round(trial.proof_completeness * 100)}%</span>
        <span>우선 확인 슬롯 · {session.current_question?.selected?.slot_id ?? "none"}</span>
        <a href="#criteria-title">판정 근거 보기</a>
        <a href={`https://clinicaltrials.gov/study/${top?.nct_id ?? trial.nct_id}`} target="_blank" rel="noreferrer">ClinicalTrials.gov ↗</a>
      </div>
    </article>
  );
}

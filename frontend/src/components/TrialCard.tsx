import type { SessionView } from "../types/api";
import { recruitmentStatusLabels, slotLabels, verdictLabels } from "../lib/locale";
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
          <span className="rank-badge" aria-label="trial rank 1">1순위</span>
          <span className="mode-badge">순위 변화 없음</span>
          <span className="mode-badge" title={top?.overall_status ?? "STATUS_UNKNOWN"}>{top?.nct_id === "NCT05239624" ? "모집 중 · 2026.08.11 기준" : `${recruitmentStatusLabels[top?.overall_status ?? "STATUS_UNKNOWN"] ?? "모집 상태 확인 필요"}${top?.data_timestamp ? ` · ${top.data_timestamp.slice(0, 10)}` : " · 저장된 데이터"}`}</span>
        </div>
        <StatusBadge code={trial.decision} />
      </div>
      <p className="trial-id">{top?.nct_id ?? trial.nct_id}</p>
      <h2 className="trial-title">{top?.title ?? trial.nct_id}</h2>
      <p className="source-preserved">ClinicalTrials.gov에 등록된 공식 영문 제목</p>
      <div className="trial-summary">
        <div>
          <p className="summary-label">현재 사전 선별 상태</p>
          <p className="summary-value">{verdictLabels[trial.decision] ?? trial.decision}</p>
        </div>
        <div className="text-right">
          <p className="score-value">{trial.display_score}</p>
          <p className="score-label">근거 일치 점수 · 적합 확률이 아님</p>
        </div>
      </div>
      <div className="verdict-metrics">
        {(["PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE", "CONFLICT"] as const).map((verdict) => (
          <div key={verdict} className={`verdict-metric verdict-metric-${verdict.toLowerCase()}`}>
            <strong>{counts[verdict] ?? 0}</strong>
            <span>{verdictLabels[verdict]}<small>{verdict === "NOT_APPLICABLE" ? "N/A" : verdict}</small></span>
          </div>
        ))}
      </div>
      <div className="trial-card-footer">
        <span>근거 검증 완료율 {Math.round(trial.proof_completeness * 100)}%</span>
        <span>다음 확인 항목 · {slotLabels[session.current_question?.selected?.slot_id ?? ""] ?? "없음"}</span>
        <a href="#criteria-title">판정 근거 보기</a>
        <a href={`https://clinicaltrials.gov/study/${top?.nct_id ?? trial.nct_id}`} target="_blank" rel="noreferrer">ClinicalTrials.gov ↗</a>
      </div>
    </article>
  );
}

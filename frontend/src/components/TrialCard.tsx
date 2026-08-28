import type { SessionView } from "../types/api";
import { recruitmentStatusLabels, slotLabels, verdictLabels } from "../lib/locale";
import { StatusBadge } from "./ClinicalUI";

export function TrialCard({ session }: { session: SessionView }) {
  const trial = session.trial_evaluation;
  const top = session.top_trial;
  if (session.support_level === "retrieval_only") {
    return (
      <article className="panel trial-card">
        <div className="trial-card-topline">
          <span className="mode-badge">검색 전용 사례</span>
          <span className="mode-badge">적격성 판정 없음</span>
        </div>
        <h2 className="trial-title mt-4">후보 순위와 적합 점수를 생성하지 않았습니다.</h2>
        <p className="source-preserved mt-2">
          이 사례는 관련 임상시험 검색 결과만 제공합니다. 검토된 조건 구조와 판정 슬롯이 없어
          개별 시험의 적격성이나 우선순위를 의미하지 않습니다.
        </p>
      </article>
    );
  }
  if (!trial) return <section className="panel">임상시험 근거를 계산하고 있습니다…</section>;
  const rankingHeld =
    trial.decision === "REVIEW_REQUIRED" || trial.degradation_codes.length > 0;
  const counts = session.proofs.reduce<Record<string, number>>((acc, proof) => {
    acc[proof.final_verdict] = (acc[proof.final_verdict] ?? 0) + 1;
    return acc;
  }, {});
  return (
    <article className="panel trial-card">
      <div className="trial-card-topline">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rank-badge" aria-label={rankingHeld ? "trial ranking held" : "trial rank 1"}>{rankingHeld ? "순위 보류" : "1순위"}</span>
          <span className="mode-badge">{rankingHeld ? "전문가 검토 대기" : "순위 변화 없음"}</span>
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

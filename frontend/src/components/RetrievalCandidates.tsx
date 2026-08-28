import { compilationStatusLabels, recruitmentStatusLabels } from "../lib/locale";
import type { RetrievalView } from "../types/api";

export function RetrievalCandidates({ retrieval }: { retrieval: RetrievalView }) {
  const selected = new Set(retrieval.selected_for_compilation);
  const candidateCount = retrieval.ranked_candidates.length;
  const selectedCount = selected.size;
  return (
    <section className="panel retrieval-candidates" aria-label="Hybrid retrieval candidates">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="eyebrow">임상시험 검색 결과</p>
          <h2
            aria-label={`${candidateCount} retained candidates · top ${selectedCount} selected`}
            className="panel-title"
          >
            검색 후보 {candidateCount}건 · {selectedCount > 0 ? `상위 ${selectedCount}건 상세 평가` : "상세 평가는 제공하지 않음"}
          </h2>
          <p className="section-description">공식 영문 제목은 원문 그대로 표시하며, 검색 점수는 임상시험 적합 확률이 아닙니다.</p>
        </div>
        <p className="text-xs text-slate-500">
          ClinicalTrials.gov API {retrieval.api_version} · {retrieval.registry_data_timestamp}
          {retrieval.dense_source_used ? " · 저장된 벡터 검색 결과" : " · 키워드 검색 대체 경로"}
        </p>
      </div>
      <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {retrieval.ranked_candidates.map((candidate, index) => (
          <article
            data-testid="retrieval-candidate"
            className={`rounded-xl border p-3 ${index < 3 ? "border-cyan-300/40 bg-cyan-300/5" : "border-slate-800 bg-slate-950/40"}`}
            key={candidate.nct_id}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-black text-cyan-300">검색 {index + 1}위</span>
              <span className="candidate-review-state" title={candidate.compilation_status}>
                {candidate.compilation_status === "OPAQUE_REVIEW_REQUIRED"
                  ? compilationStatusLabels.OPAQUE_REVIEW_REQUIRED
                  : selected.has(candidate.nct_id)
                    ? "상세 평가 대상으로 선택"
                    : "검색 후보로 보존"}
              </span>
            </div>
            <p className="mt-2 text-xs font-bold text-slate-200">{candidate.nct_id}</p>
            <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-400">
              {candidate.trial.brief_title}
            </p>
            <div className="mt-2 flex items-center justify-between text-[0.65rem] text-slate-500">
              <span title={candidate.trial.overall_status}>{recruitmentStatusLabels[candidate.trial.overall_status] ?? candidate.trial.overall_status.replaceAll("_", " ")}</span>
              <span>검색 점수 {candidate.retrieval_score.toFixed(3)}</span>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

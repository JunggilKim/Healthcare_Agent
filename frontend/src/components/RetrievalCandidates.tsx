import type { RetrievalView } from "../types/api";

export function RetrievalCandidates({ retrieval }: { retrieval: RetrievalView }) {
  const selected = new Set(retrieval.selected_for_compilation);
  return (
    <section className="panel retrieval-candidates" aria-label="Hybrid retrieval candidates">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="eyebrow">제한된 하이브리드 검색 · BOUNDED HYBRID RETRIEVAL</p>
          <h2 className="panel-title">20 retained candidates · top 8 selected</h2>
        </div>
        <p className="text-xs text-slate-500">
          CTGov API {retrieval.api_version} · {retrieval.registry_data_timestamp}
          {retrieval.dense_source_used ? " · recorded dense fixture" : " · lexical fallback"}
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
              <span className="text-xs font-black text-cyan-300">#{index + 1}</span>
              <span className="text-[0.62rem] font-bold uppercase text-slate-500">
                {candidate.compilation_status === "OPAQUE_REVIEW_REQUIRED"
                  ? "opaque · review required"
                  : selected.has(candidate.nct_id)
                    ? "selected · not compiled"
                    : "retained"}
              </span>
            </div>
            <p className="mt-2 text-xs font-bold text-slate-200">{candidate.nct_id}</p>
            <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-400">
              {candidate.trial.brief_title}
            </p>
            <div className="mt-2 flex items-center justify-between text-[0.65rem] text-slate-500">
              <span>{candidate.trial.overall_status.replaceAll("_", " ")}</span>
              <span>{candidate.retrieval_score.toFixed(3)}</span>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

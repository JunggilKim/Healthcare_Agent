import type { SessionView } from "../types/api";
import { criterionKorean } from "../lib/locale";
import { SourceOriginal, StatusBadge } from "./ClinicalUI";

const labels: Record<string, string> = {
  "NCT05239624:INCLUSION:001:443174ab": "Age ≥ 18 years",
  "NCT05239624:INCLUSION:002:5f52ab88": "Pathology-confirmed urothelial histology",
  "NCT05239624:INCLUSION:003:a7db6608": "Muscle-invasive disease",
  "NCT05239624:INCLUSION:004:2b0a94f9": "Allowed clinical TNM stage",
  "NCT05239624:INCLUSION:005:dac9ad49": "No prior MIBC systemic treatment",
  "NCT05239624:INCLUSION:006:ba33ff17": "ECOG 0–1",
  "NCT05239624:INCLUSION:007:53a2629b": "GFR or CrCl ≥ 30 mL/min",
};

export function CriterionMatrix({ session }: { session: SessionView }) {
  return (
    <section className="panel criterion-panel min-h-0 overflow-hidden" aria-labelledby="criteria-title">
      <div className="criterion-heading">
        <div><p className="eyebrow">REPLAYABLE EVIDENCE TRAIL</p><h2 id="criteria-title" aria-label="Criterion proof table" className="panel-title">조건별 근거 증명</h2></div>
        <span className="mode-badge">조건별 근거 증명 · Criterion Proof</span>
      </div>
      <div
        aria-label="Criterion proof table horizontal scroll area"
        className="criterion-scroll"
        tabIndex={0}
      >
        <table className="criterion-table w-full text-left text-sm">
          <thead>
            <tr><th>조건 · Requirement</th><th>판정 · Verdict</th><th>환자 근거 · Evidence</th><th>검증·다음 정보</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {session.proofs.map((proof) => (
              <tr key={proof.criterion_id}>
                <td className="criterion-requirement"><span className="criterion-official">{labels[proof.criterion_id] ?? proof.criterion_id}</span><span className="criterion-ko">{criterionKorean[proof.criterion_id] ?? "한국어 설명 없음"}</span><span className="criterion-normalized">{session.criteria.find((item) => item.criterion_id === proof.criterion_id)?.normalized_summary}</span>
                  {(() => {
                    const criterion = session.criteria.find((item) => item.criterion_id === proof.criterion_id);
                    return <SourceOriginal label={`Registry ${criterion?.source_direction.toLowerCase() ?? "eligibility"} · 영어 원문`}><dl><div><dt>Exact registry text</dt><dd>{criterion?.source_quote ?? "Unavailable"}</dd></div><div><dt>Source hash</dt><dd className="font-mono">{proof.criterion_source_hash.slice(0, 12)}…</dd></div><div><dt>AST / derivation</dt><dd>{criterion?.ast.nodes.length ?? 0} AST node(s) · {proof.derivation_steps.length} deterministic step(s)</dd></div></dl></SourceOriginal>;
                  })()}
                </td>
                <td><StatusBadge code={proof.final_verdict} /></td>
                <td><span className="evidence-facts">{proof.evidence_fact_ids.length ? proof.evidence_fact_ids.join(", ") : "허용된 근거 없음 · No admissible fact"}</span><span className="evidence-grade">등급 · {proof.evidence_fact_ids.map((factId) => session.facts.find((fact) => fact.fact_id === factId)?.grade ?? "?").join(", ") || "—"}</span></td>
                <td><span aria-label="Verifier checks" className="verifier-ok"><span>{proof.verifier_checks.filter((check) => check.applicable && check.passed).length}/{proof.verifier_checks.filter((check) => check.applicable).length} applicable ✓</span></span><span className="missing-slot"><span>다음 슬롯 · </span><span>{proof.missing_slot_ids.join(", ") || "None"}</span></span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

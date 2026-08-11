import type { SessionView } from "../types/api";

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
    <section className="panel min-h-0 overflow-hidden p-3" aria-labelledby="criteria-title">
      <p className="eyebrow">REPLAYABLE EVIDENCE TRAIL</p>
      <h2 id="criteria-title" className="mt-1 text-base font-bold">Criterion proof table</h2>
      <div
        aria-label="Criterion proof table horizontal scroll area"
        className="mt-2 max-h-[360px] overflow-auto"
        tabIndex={0}
      >
        <table className="w-full min-w-[1080px] text-left text-sm">
          <thead className="text-xs uppercase tracking-wider text-slate-500">
            <tr><th className="pb-3">Criterion source</th><th className="pb-3">Normalized requirement</th><th className="pb-3">Verdict</th><th className="pb-3">Patient evidence</th><th className="pb-3">Evidence grade</th><th className="pb-3">Verifier status</th><th className="pb-3">Required next information</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {session.proofs.map((proof) => (
              <tr key={proof.criterion_id}>
                <td className="py-2 pr-3 text-xs text-slate-400">
                  {(() => {
                    const criterion = session.criteria.find((item) => item.criterion_id === proof.criterion_id);
                    return <details><summary className="cursor-pointer text-cyan-200">Registry {criterion?.source_direction.toLowerCase() ?? "eligibility"}</summary><dl className="mt-2 max-w-72 space-y-1"><div><dt className="font-bold">Exact registry text</dt><dd>{criterion?.source_quote ?? "Unavailable"}</dd></div><div><dt className="font-bold">Source hash</dt><dd className="font-mono">{proof.criterion_source_hash.slice(0, 12)}…</dd></div><div><dt className="font-bold">AST / derivation</dt><dd>{criterion?.ast.nodes.length ?? 0} AST node(s) · {proof.derivation_steps.length} deterministic step(s)</dd></div></dl></details>;
                  })()}
                </td>
                <td className="py-2 pr-3 text-xs text-slate-200"><span className="block font-semibold">{labels[proof.criterion_id] ?? proof.criterion_id}</span><span className="mt-1 block text-slate-400">{session.criteria.find((item) => item.criterion_id === proof.criterion_id)?.normalized_summary}</span></td>
                <td className="py-2 pr-3"><span className={`verdict verdict-${proof.final_verdict.toLowerCase()}`}>{proof.final_verdict}</span></td>
                <td className="py-2 pr-3 text-xs text-slate-400">{proof.evidence_fact_ids.length ? proof.evidence_fact_ids.join(", ") : "No admissible fact"}</td>
                <td className="py-2 pr-3 text-xs text-slate-300">{proof.evidence_fact_ids.map((factId) => session.facts.find((fact) => fact.fact_id === factId)?.grade ?? "?").join(", ") || "—"}</td>
                <td className="py-2 pr-3 text-xs text-emerald-300">{proof.verifier_checks.filter((check) => check.applicable && check.passed).length}/{proof.verifier_checks.filter((check) => check.applicable).length} applicable ✓</td>
                <td className="py-2 text-xs text-amber-100">{proof.missing_slot_ids.join(", ") || "None"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

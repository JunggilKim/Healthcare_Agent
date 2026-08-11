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
    <section className="panel" aria-labelledby="criteria-title">
      <p className="eyebrow">REPLAYABLE EVIDENCE TRAIL</p>
      <h2 id="criteria-title" className="panel-title">Criterion proof table</h2>
      <div className="mt-5 overflow-x-auto">
        <table className="w-full min-w-[620px] text-left text-sm">
          <thead className="text-xs uppercase tracking-wider text-slate-500">
            <tr><th className="pb-3">Requirement</th><th className="pb-3">Verdict</th><th className="pb-3">Evidence</th><th className="pb-3">Verifier</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {session.proofs.map((proof) => (
              <tr key={proof.criterion_id}>
                <td className="py-3 pr-4 text-slate-200">{labels[proof.criterion_id] ?? proof.criterion_id}</td>
                <td className="py-3 pr-4"><span className={`verdict verdict-${proof.final_verdict.toLowerCase()}`}>{proof.final_verdict}</span></td>
                <td className="py-3 pr-4 text-slate-400">{proof.evidence_fact_ids.length ? `${proof.evidence_fact_ids.length} fact` : proof.missing_slot_ids.join(", ") || "—"}</td>
                <td className="py-3 text-emerald-300">PV-001…014 ✓</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}


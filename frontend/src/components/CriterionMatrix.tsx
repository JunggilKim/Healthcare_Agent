import type { SessionView } from "../types/api";
import { criterionKorean, slotLabels } from "../lib/locale";
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
  const empty = session.proofs.length === 0;
  return (
    <section className="panel criterion-panel min-h-0 overflow-hidden" aria-labelledby="criteria-title">
      <div className="criterion-heading">
        <div><p className="eyebrow">조건별 판정 근거</p><h2 id="criteria-title" aria-label="Criterion proof table" className="panel-title">어떤 조건이 확인됐고, 무엇이 부족한가요?</h2><p className="section-description">등록 기준 원문은 그대로 보존하고, 현재 환자 기록으로 확인 가능한 근거만 연결합니다.</p></div>
        <span className="mode-badge">{session.proofs.length}개 선정 조건</span>
      </div>
      {empty ? (
        <p className="section-description mt-4">
          이 검색 전용 사례에서는 조건 구조화와 적격성 판정을 실행하지 않았습니다.
        </p>
      ) : (
      <div
        aria-label="Criterion proof table horizontal scroll area"
        className="criterion-scroll"
        tabIndex={0}
      >
        <table className="criterion-table w-full text-left text-sm">
          <thead>
            <tr><th>임상시험 선정 조건</th><th>현재 판정</th><th>연결된 환자 근거</th><th>검증 결과·다음 확인</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {session.proofs.map((proof) => (
              <tr key={proof.criterion_id}>
                <td className="criterion-requirement"><span className="criterion-ko">{criterionKorean[proof.criterion_id] ?? "한국어 설명 없음"}</span><span className="criterion-direction">등록 기준 · 선정 조건</span>
                  {(() => {
                    const criterion = session.criteria.find((item) => item.criterion_id === proof.criterion_id);
                    return <SourceOriginal><dl><div><dt>간결한 영문 조건명</dt><dd>{labels[proof.criterion_id] ?? proof.criterion_id}</dd></div><div><dt>ClinicalTrials.gov 등록 원문</dt><dd>{criterion?.source_quote ?? "Unavailable"}</dd></div><div><dt>정규화된 조건</dt><dd>{criterion?.normalized_summary ?? "Unavailable"}</dd></div><div><dt>출처 해시</dt><dd className="font-mono">{proof.criterion_source_hash.slice(0, 12)}…</dd></div><div><dt>검증 구조</dt><dd>AST 노드 {criterion?.ast.nodes.length ?? 0}개 · 결정론적 도출 단계 {proof.derivation_steps.length}개</dd></div></dl></SourceOriginal>;
                  })()}
                </td>
                <td><StatusBadge code={proof.final_verdict} /></td>
                <td><span className="evidence-facts">{proof.evidence_fact_ids.length ? `${proof.evidence_fact_ids.length}개 환자 근거 연결됨` : "연결할 수 있는 환자 근거 없음"}</span>{proof.evidence_fact_ids.length ? <SourceOriginal label="근거 ID 보기"><p className="font-mono">{proof.evidence_fact_ids.join(", ")}</p></SourceOriginal> : null}<span className="evidence-grade">근거 등급 · {proof.evidence_fact_ids.map((factId) => session.facts.find((fact) => fact.fact_id === factId)?.grade ?? "?").join(", ") || "—"}</span></td>
                <td><span aria-label="Verifier checks" className="verifier-ok"><span>자동 검증 {proof.verifier_checks.filter((check) => check.applicable && check.passed).length}/{proof.verifier_checks.filter((check) => check.applicable).length} 통과</span></span><span className="missing-slot"><span>다음 확인 · </span><span>{proof.missing_slot_ids.length ? proof.missing_slot_ids.map((slot) => slotLabels[slot] ?? slot).join(", ") : "추가 확인 없음"}</span></span>{proof.missing_slot_ids.length ? <span className="missing-slot-code">{proof.missing_slot_ids.join(", ")}</span> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      )}
    </section>
  );
}

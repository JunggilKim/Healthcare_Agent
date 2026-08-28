import type { SessionView } from "../types/api";

function confirmedHistology(session: SessionView): boolean {
  const criterionIds = new Set(
    session.criteria
      .filter((criterion) =>
        /histolog|조직형/i.test(`${criterion.normalized_summary} ${criterion.source_quote}`),
      )
      .map((criterion) => criterion.criterion_id),
  );
  const proof = session.proofs.find((item) => criterionIds.has(item.criterion_id));
  if (!proof || proof.final_verdict !== "PASS") return false;
  return proof.evidence_fact_ids.some(
    (factId) => session.facts.find((fact) => fact.fact_id === factId)?.grade === "A",
  );
}

export function EvidenceFirewall({ session }: { session: SessionView }) {
  const pathologyConfirmed = confirmedHistology(session);
  return (
    <section className="panel firewall-panel">
      <p className="eyebrow">근거 안전장치</p>
      {pathologyConfirmed ? (
        <>
          <h2>확인된 병리 근거만 조직형 판정에 반영합니다.</h2>
          <p>
            CT 소견은 계속 검색 단서로만 사용합니다. 조직형 PASS는 연결된 Grade A 병리 근거에서
            도출됐고, 근육 침윤처럼 확인되지 않은 조건은 UNKNOWN으로 유지합니다. 검증 규칙{" "}
            <span className="status-code">PV-007</span>이 영상 기반 추정 진단의 판정 사용을
            차단합니다.
          </p>
        </>
      ) : (
        <>
          <h2>영상 소견만으로 병리 진단을 확정하지 않습니다.</h2>
          <p>
            CT에서 방광 종괴가 관찰되었지만, 병리검사 결과가 없으므로 조직형은 아직 확인되지
            않은 상태로 유지합니다. 검증 규칙 <span className="status-code">PV-007</span>이 추정
            정보가 최종 판정 근거로 사용되지 않도록 차단합니다.
          </p>
        </>
      )}
    </section>
  );
}

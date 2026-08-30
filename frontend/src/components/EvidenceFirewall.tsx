import type { SessionView } from "../types/api";

type FirewallFlowItem = {
  source: string;
  outcome: string;
};

function FirewallFlow({ items }: { items: FirewallFlowItem[] }) {
  return (
    <div className="firewall-flow" aria-label="검색 단서와 판정 근거 분리 흐름">
      {items.map((item) => (
        <div className="firewall-flow-item" key={`${item.source}:${item.outcome}`}>
          <span>{item.source}</span>
          <span aria-hidden="true">→</span>
          <strong>{item.outcome}</strong>
        </div>
      ))}
    </div>
  );
}

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
  if (session.support_level === "retrieval_only") {
    return (
      <section className="panel firewall-panel" aria-labelledby="evidence-firewall-title">
        <p className="eyebrow">Evidence Firewall · 근거 안전장치</p>
        <h2 id="evidence-firewall-title">검색 가설은 적격성 판정 근거가 아닙니다.</h2>
        <p>
          합성 사례의 질환 개념은 ClinicalTrials.gov 검색에만 사용합니다. 조건 구조와 판정 슬롯이
          검토되지 않은 상태에서는 검색 후보를 PASS·FAIL로 평가하거나 우선순위를 생성하지
          않습니다.
        </p>
        <FirewallFlow
          items={[
            { source: "질환 검색 가설", outcome: "임상시험 검색에만 사용" },
            { source: "검토되지 않은 조건", outcome: "PASS·FAIL 판정 차단" },
          ]}
        />
      </section>
    );
  }
  const hasHistologyCriterion = session.criteria.some((criterion) =>
    /histolog|조직형/i.test(`${criterion.normalized_summary} ${criterion.source_quote}`),
  );
  if (!hasHistologyCriterion) {
    return (
      <section className="panel firewall-panel" aria-labelledby="evidence-firewall-title">
        <p className="eyebrow">Evidence Firewall · 근거 안전장치</p>
        <h2 id="evidence-firewall-title">검색 단서와 판정 근거를 분리합니다.</h2>
        <p>
          Grade H 검색 가설은 후보 탐색에만 사용합니다. 확인되지 않은 조건은 UNKNOWN으로
          유지하고, 출처가 연결된 근거와 검증된 프로토콜만 조건별 판정에 반영합니다.
        </p>
        <FirewallFlow
          items={[
            { source: "Grade H 검색 가설", outcome: "후보 탐색에만 사용" },
            { source: "확인되지 않은 조건", outcome: "UNKNOWN 유지" },
          ]}
        />
      </section>
    );
  }
  const pathologyConfirmed = confirmedHistology(session);
  return (
    <section className="panel firewall-panel" aria-labelledby="evidence-firewall-title">
      <p className="eyebrow">Evidence Firewall · 근거 안전장치</p>
      {pathologyConfirmed ? (
        <>
          <h2 id="evidence-firewall-title">확인된 병리 근거만 조직형 판정에 반영합니다.</h2>
          <p>
            CT 소견은 계속 검색 단서로만 사용합니다. 조직형 PASS는 연결된 Grade A 병리 근거에서
            도출됐고, 근육 침윤처럼 확인되지 않은 조건은 UNKNOWN으로 유지합니다. 검증 규칙{" "}
            <span className="status-code">PV-007</span>이 영상 기반 추정 진단의 판정 사용을
            차단합니다.
          </p>
          <FirewallFlow
            items={[
              { source: "CT 영상 소견", outcome: "검색 단서로만 사용" },
              { source: "Grade A 병리 근거", outcome: "조직형 PASS 허용" },
            ]}
          />
        </>
      ) : (
        <>
          <h2 id="evidence-firewall-title">영상 소견만으로 병리 진단을 확정하지 않습니다.</h2>
          <p>
            CT에서 방광 종괴가 관찰되었지만, 병리검사 결과가 없으므로 조직형은 아직 확인되지
            않은 상태로 유지합니다. 검증 규칙 <span className="status-code">PV-007</span>이 추정
            정보가 최종 판정 근거로 사용되지 않도록 차단합니다.
          </p>
          <FirewallFlow
            items={[
              { source: "CT 영상 소견", outcome: "검색 단서로만 사용" },
              { source: "병리 기록 없음", outcome: "조직형 UNKNOWN 유지" },
            ]}
          />
        </>
      )}
    </section>
  );
}

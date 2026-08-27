export const DISCLAIMER =
  "이 시스템은 공개 및 합성 데이터만 사용하는 임상시험 사전 선별 연구 프로토타입입니다. " +
  "질병을 진단하거나 의학적 조언을 제공하지 않으며, 최종 참여 자격을 결정하지 않습니다. " +
  "최종 판단은 자격을 갖춘 임상시험 팀의 검토가 필요합니다.";

export function Disclaimer() {
  return (
    <aside aria-label="의료 및 데이터 안전 고지" className="clinical-disclaimer">
      <span aria-hidden="true" className="disclaimer-symbol">△</span>
      <div><h2>연구용 사전 선별 지원 데모</h2><p>{DISCLAIMER}</p></div>
    </aside>
  );
}

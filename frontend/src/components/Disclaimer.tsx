export const DISCLAIMER =
  "이 시스템은 공개 및 합성 데이터만 사용하는 임상시험 사전 선별 연구 프로토타입입니다. " +
  "질병을 진단하거나 의학적 조언을 제공하지 않으며, 최종 참여 자격을 결정하지 않습니다. " +
  "최종 판단은 자격을 갖춘 임상시험 팀의 검토가 필요합니다.";

export function Disclaimer() {
  return (
    <aside aria-label="의료 및 데이터 안전 고지" className="rounded-2xl border border-amber-300/40 bg-amber-100/10 p-5">
      <h2 className="font-semibold text-amber-200">연구용 사전 선별 · 의료 조언 아님</h2>
      <p className="mt-2 leading-7 text-slate-200">{DISCLAIMER}</p>
    </aside>
  );
}


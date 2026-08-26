export const ko = {
  product: {
    name: "TRIAL-OPT",
    descriptor: "Clinical Intelligence Demo",
    prototypeNotice: "연구용 사전 선별 지원 데모",
    nonClinical: "최종 임상시험 적격 판정 또는 의료 조언이 아닙니다.",
  },
  mode: {
    snapshot: "스냅샷 데모 (Snapshot Demo)",
    live: "라이브 모드 (Live Mode)",
  },
  navigation: {
    patient: "환자·판정",
    research: "연구 근거",
    experiment: "실험 근거",
  },
  action: {
    replay: "Proof 다시 실행 (Replay Proof)",
    export: "보고서 내보내기 (Export)",
    reset: "세션 초기화",
    delete: "세션 삭제",
    viewOriginal: "영어 원문 보기",
  },
} as const;

export const stageLabels: Record<string, { ko: string; en: string }> = {
  "Patient Evidence": { ko: "환자 근거", en: "Patient Evidence" },
  "Trial Retrieval": { ko: "임상시험 검색", en: "Trial Retrieval" },
  "Protocol Compilation": { ko: "프로토콜 컴파일", en: "Protocol Compilation" },
  "Eligibility Proof": { ko: "적격성 Proof", en: "Eligibility Proof" },
  "Proof Verification": { ko: "Proof 검증", en: "Proof Verification" },
  Ranking: { ko: "순위 계산", en: "Ranking" },
  "Next Question Optimization": { ko: "다음 질문 최적화", en: "Next Question Optimization" },
};

export const stageStateLabels = {
  pending: "대기",
  running: "분석 중",
  completed: "완료",
  degraded: "대체 경로",
  failed: "실패",
} as const;

export const verdictLabels: Record<string, string> = {
  PASS: "충족",
  FAIL: "불충족",
  UNKNOWN: "확인 필요",
  NOT_APPLICABLE: "해당 없음",
  CONFLICT: "근거 충돌",
  POTENTIAL_MATCH: "잠재적 적합",
};

export function localizedCode(code: string): string {
  return verdictLabels[code] ? `${verdictLabels[code]} · ${code}` : code;
}

export const criterionKorean: Record<string, string> = {
  "NCT05239624:INCLUSION:001:443174ab": "만 18세 이상",
  "NCT05239624:INCLUSION:002:5f52ab88": "병리검사로 확인된 요로상피암 조직형",
  "NCT05239624:INCLUSION:003:a7db6608": "근육 침윤성 질환",
  "NCT05239624:INCLUSION:004:2b0a94f9": "허용되는 임상 TNM 병기",
  "NCT05239624:INCLUSION:005:dac9ad49": "MIBC 전신 치료 이력 없음",
  "NCT05239624:INCLUSION:006:ba33ff17": "ECOG 수행 상태 0–1",
  "NCT05239624:INCLUSION:007:53a2629b": "GFR 또는 CrCl 30 mL/min 이상",
};

export const runtimeLabels: Record<string, string> = {
  DEGRADED: "성능 저하 · DEGRADED",
  FALLBACK: "대체 경로 · FALLBACK",
  LOADING: "분석 중 · LOADING",
  ERROR: "오류 · ERROR",
  EMPTY: "표시할 근거 없음 · EMPTY",
};

export const ko = {
  product: {
    name: "TRIAL-OPT",
    descriptor: "임상시험 사전 선별 데모",
    prototypeNotice: "연구용 사전 선별 지원 데모",
    nonClinical: "최종 임상시험 적격 판정 또는 의료 조언이 아닙니다.",
  },
  mode: {
    snapshot: "스냅샷 데모 (Snapshot)",
    snapshotShort: "스냅샷 데모",
    live: "라이브 모드 (Live)",
    liveShort: "라이브 모드",
  },
  navigation: {
    prescreen: "사전 선별",
    patient: "환자·판정",
    research: "연구 근거",
    experiment: "실험 근거",
  },
  action: {
    replay: "판정 다시 검증",
    export: "보고서 저장",
    reset: "처음부터 다시",
    delete: "세션 삭제",
    viewOriginal: "영어 원문 보기",
  },
  landing: {
    eyebrow: "근거 중심 임상시험 사전 선별",
    title: "근거가 부족한 지점을 찾고,\n다음 확인 질문을 제안합니다.",
    description:
      "환자 설명을 임의로 보완하지 않습니다. 현재 기록으로 확인할 수 있는 조건과 아직 근거가 부족한 조건을 구분하고, 판정에 가장 도움이 되는 기존 기록 한 가지를 제안합니다.",
  },
} as const;

export const stageLabels: Record<string, { ko: string; en: string }> = {
  "Patient Evidence": { ko: "환자 정보 정리", en: "Patient Evidence" },
  "Trial Retrieval": { ko: "임상시험 검색", en: "Trial Retrieval" },
  "Protocol Compilation": { ko: "선정 기준 구조화", en: "Protocol Compilation" },
  "Eligibility Proof": { ko: "조건별 근거 평가", en: "Eligibility Proof" },
  "Proof Verification": { ko: "판정 근거 검증", en: "Proof Verification" },
  Ranking: { ko: "우선순위 계산", en: "Ranking" },
  "Next Question Optimization": { ko: "다음 질문 선택", en: "Next Question Optimization" },
};

export const stageStateLabels = {
  pending: "대기",
  running: "진행 중",
  completed: "완료",
  degraded: "대체 경로 사용",
  failed: "실패",
} as const;

export const verdictLabels: Record<string, string> = {
  PASS: "조건 충족",
  FAIL: "조건 불충족",
  UNKNOWN: "근거 부족",
  NOT_APPLICABLE: "해당 없음",
  CONFLICT: "근거 충돌",
  POTENTIAL_MATCH: "잠재적 적합",
};

export function localizedCode(code: string): string {
  return verdictLabels[code] ? `${verdictLabels[code]} · ${code}` : code;
}

export const criterionKorean: Record<string, string> = {
  "NCT05239624:INCLUSION:001:443174ab": "만 18세 이상",
  "NCT05239624:INCLUSION:002:5f52ab88": "병리검사로 요로상피암 조직형이 확인됨",
  "NCT05239624:INCLUSION:003:a7db6608": "근육 침윤성 방광암",
  "NCT05239624:INCLUSION:004:2b0a94f9": "허용 범위에 해당하는 임상 TNM 병기",
  "NCT05239624:INCLUSION:005:dac9ad49": "근육 침윤성·전이성 요로상피암 전신 치료 이력 없음",
  "NCT05239624:INCLUSION:006:ba33ff17": "ECOG 활동도 0–1",
  "NCT05239624:INCLUSION:007:53a2629b": "GFR 또는 CrCl 30 mL/min 이상",
};

export const slotLabels: Record<string, string> = {
  "pathology.histology": "병리 조직형",
  "pathology.muscle_invasion": "근육 침윤 여부",
  "staging.clinical_group": "임상 TNM 병기",
  "prior_treatment.mibc_systemic": "이전 전신 치료",
  "performance_status.ecog": "ECOG 활동도",
  "organ_function.renal.gfr_or_crcl": "신장 기능 수치",
};

export const questionActionLabels: Record<string, string> = {
  REQUEST_RECORD: "기존 기록 확인",
  ASK_PATIENT: "환자에게 확인",
  ASK_CLINICIAN: "의료진에게 확인",
};

const questionValueLabels: Record<string, string> = {
  true: "기존 기록에서 확인됨",
  false: "기존 기록에서 확인되지 않음",
  urothelial_carcinoma: "요로상피암",
  other_histology: "다른 조직형",
  unknown: "확인 필요",
};

export function localizedQuestionValue(value: string): string {
  return questionValueLabels[value] ?? value;
}

export const recruitmentStatusLabels: Record<string, string> = {
  RECRUITING: "모집 중",
  NOT_YET_RECRUITING: "모집 예정",
  ENROLLING_BY_INVITATION: "초청 등록",
  ACTIVE_NOT_RECRUITING: "진행 중·모집 종료",
  COMPLETED: "종료",
  STATUS_UNKNOWN: "모집 상태 확인 필요",
};

export const compilationStatusLabels: Record<string, string> = {
  OPAQUE_REVIEW_REQUIRED: "원문 검토 필요",
  NOT_COMPILED: "구조화 대기",
  VERIFIED: "구조화 검증 완료",
};

export const casePresentation: Record<string, { summary: string; availability: string }> = {
  S001: { summary: "54세 남성 · 심한 명치 통증과 구토 · 췌장효소 상승", availability: "전체 데모 제공" },
  S002: { summary: "29세 여성 · 두근거림과 체중 감소 · 갑상선 비대", availability: "검색 경로만 제공" },
  S003: { summary: "7세 남아 · 눈 주위 부종과 단백뇨 · 저알부민혈증", availability: "검색 경로만 제공" },
  S004: { summary: "68세 남성 · 무통성 혈뇨 · CT에서 방광벽 종괴 관찰", availability: "전체 데모 제공" },
  S005: { summary: "34세 여성 · 시야 증상 뒤 반복되는 편측 두통", availability: "검색 경로만 제공" },
  S006: { summary: "45세 남성 · 조절되지 않는 당뇨 · 안면 통증과 괴사성 병변", availability: "검색 경로만 제공" },
  S007: { summary: "3개월 영아 · 수유 후 분수성 구토 · 대사성 알칼리증", availability: "검색 경로만 제공" },
  S008: { summary: "60세 여성 · 진행성 호흡곤란 · CT에서 벌집모양 음영", availability: "전체 데모 제공" },
  S009: { summary: "19세 남성 · 발열과 인후통 · 경부 림프절병증", availability: "검색 경로만 제공" },
  S010: { summary: "73세 남성 · 갑작스러운 무통성 시야 소실", availability: "검색 경로만 제공" },
};

export const pipelineEventLabels: Record<string, string> = {
  fact_extracted: "환자 정보 정리 완료",
  retrieval_completed: "임상시험 검색 완료",
  trial_compiled: "선정 기준 구조화 완료",
  trial_evaluated: "조건별 근거 평가 완료",
  proof_verified: "판정 근거 검증 완료",
  rankings_updated: "우선순위 계산 완료",
  question_selected: "다음 확인 질문 선택 완료",
};

export const runtimeLabels: Record<string, string> = {
  DEGRADED: "일부 기능 제한 · DEGRADED",
  FALLBACK: "대체 경로 사용 · FALLBACK",
  LOADING: "분석 중 · LOADING",
  ERROR: "오류 · ERROR",
  EMPTY: "표시할 근거 없음 · EMPTY",
};

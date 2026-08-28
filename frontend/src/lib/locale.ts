export const ko = {
  product: {
    name: "TRIAL-OPT",
    descriptor: "임상시험 근거 분석 데모",
    prototypeNotice: "연구용 임상시험 사전 선별 데모",
    nonClinical: "최종 임상시험 적격 판정 또는 의료 조언이 아닙니다.",
  },
  mode: {
    snapshot: "스냅샷 데모 (Snapshot Demo)",
    snapshotShort: "스냅샷 데모",
    live: "라이브 모드 (Live Mode)",
    liveShort: "라이브 모드",
  },
  navigation: {
    prescreen: "사전 선별",
    patient: "환자·판정",
    research: "연구 근거",
    experiment: "실험 근거",
  },
  action: {
    replay: "근거 다시 검증",
    export: "보고서 내보내기",
    reset: "분석 초기화",
    delete: "세션 삭제",
    viewOriginal: "영어 원문 보기",
  },
  landing: {
    eyebrow: "검증 가능한 임상시험 사전 선별",
    title: "환자 정보에서\n검증 가능한 임상시험 근거까지",
    description:
      "환자 정보를 임의로 보완하지 않습니다. 현재 기록으로 확인된 조건과 근거가 부족한 조건을 나누고, 판정을 갱신하는 데 가장 유용한 다음 확인 항목을 제안합니다.",
  },
} as const;

export const stageLabels: Record<string, { ko: string; en: string }> = {
  "Patient Evidence": { ko: "환자 정보 정규화", en: "Patient Evidence" },
  "Trial Retrieval": { ko: "임상시험 검색", en: "Trial Retrieval" },
  "Protocol Compilation": { ko: "선정 조건 구조화", en: "Protocol Compilation" },
  "Eligibility Proof": { ko: "적격 조건 대조", en: "Eligibility Proof" },
  "Proof Verification": { ko: "판정 근거 검증", en: "Proof Verification" },
  Ranking: { ko: "후보 우선순위", en: "Ranking" },
  "Next Question Optimization": { ko: "추가 확인 질문", en: "Next Question Optimization" },
};

export const stageStateLabels = {
  pending: "대기",
  running: "진행 중",
  completed: "완료",
  degraded: "대체 경로 사용",
  failed: "실패",
  skipped: "검색 전용 범위 밖",
} as const;

export const verdictLabels: Record<string, string> = {
  PASS: "조건 충족",
  FAIL: "조건 불충족",
  UNKNOWN: "근거 부족",
  NOT_APPLICABLE: "해당 없음",
  CONFLICT: "근거 충돌",
  PRE_SCREEN_PASS: "확인된 핵심 조건 충족",
  POTENTIAL_MATCH: "잠재적 적합",
  REVIEW_REQUIRED: "전문가 검토 필요",
  INELIGIBLE: "확인된 핵심 조건 불충족",
  IRRELEVANT: "검색 관련성 낮음",
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
  "smoking.pack_years": "누적 흡연량",
  "occupation.high_risk_exposure_years": "고위험 직업성 노출 기간",
  "medical_history.genitourinary_cancer": "비뇨기계 암 과거력",
  "procedure.hematuria_evaluation_within_2_years": "최근 혈뇨 평가 이력",
  "consent.informed_provided": "연구 참여 동의 절차",
};

export const questionActionLabels: Record<string, string> = {
  REQUEST_RECORD: "기존 기록 확인",
  ASK_PATIENT: "환자에게 확인",
  ASK_CLINICIAN: "의료진에게 확인",
};

const questionValueLabels: Record<string, string> = {
  urothelial_carcinoma: "요로상피암",
  other_histology: "다른 조직형",
  unknown: "확인 필요",
};

interface QuestionValueContext {
  action?: string;
  slotId?: string;
}

const patientBooleanLabels: Record<string, [string, string]> = {
  "consent.informed_provided": ["예, 동의했습니다", "아니요, 동의하지 않았습니다"],
  "prior_treatment.mibc_systemic": [
    "예, 치료받은 적이 있습니다",
    "아니요, 치료받은 적이 없습니다",
  ],
  "medical_history.genitourinary_cancer": [
    "예, 진단받은 적이 있습니다",
    "아니요, 진단받은 적이 없습니다",
  ],
  "procedure.hematuria_evaluation_within_2_years": [
    "예, 검사를 받은 적이 있습니다",
    "아니요, 검사받은 적이 없습니다",
  ],
};

export function localizedQuestionValue(
  value: string,
  context: QuestionValueContext = {},
): string {
  if (value === "true" || value === "false") {
    if (context.action === "ASK_PATIENT") {
      const labels = patientBooleanLabels[context.slotId ?? ""] ?? [
        "예, 그렇습니다",
        "아니요, 그렇지 않습니다",
      ];
      return value === "true" ? labels[0] : labels[1];
    }
    if (context.action === "ASK_CLINICIAN") {
      return value === "true" ? "해당함" : "해당하지 않음";
    }
    return value === "true" ? "기록에서 확인됨" : "기록에서 확인되지 않음";
  }
  const syntheticYears = value.match(/^synthetic:(\d+(?:\.\d+)?)\s*year$/i);
  if (syntheticYears) return `${syntheticYears[1]}년`;
  const syntheticPackYears = value.match(/^synthetic:(\d+(?:\.\d+)?)\s*pack[- ]?years?$/i);
  if (syntheticPackYears) return `${syntheticPackYears[1]}갑년`;
  return questionValueLabels[value] ?? value;
}

export function questionFallbackLabels(action: string): {
  unknown: string;
  declined: string;
} {
  if (action === "ASK_PATIENT") {
    return { unknown: "잘 모르겠습니다", declined: "답변하지 않겠습니다" };
  }
  if (action === "ASK_CLINICIAN") {
    return { unknown: "의료진 확인이 필요함", declined: "현재 확인할 수 없음" };
  }
  return { unknown: "기록 내용이 불명확함", declined: "기록을 확인할 수 없음" };
}

export function questionNotice(action: string): string {
  if (action === "ASK_PATIENT") {
    return "새 검사나 진단을 요청하지 않습니다. 현재 알고 있거나 보유한 기록으로 답할 수 있는 내용만 묻습니다.";
  }
  if (action === "ASK_CLINICIAN") {
    return "새 검사를 권하는 항목이 아닙니다. 현재 진료기록으로 확인 가능한 내용만 묻습니다.";
  }
  return "새 검사를 권하는 항목이 아닙니다. 이미 보유한 기록에서 확인 가능한 내용만 묻습니다.";
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

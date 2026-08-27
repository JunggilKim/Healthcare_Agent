import { describe, expect, it } from "vitest";

import { localizedCode, localizedQuestionValue, stageLabels, verdictLabels } from "./locale";

describe("Korean display mapping", () => {
  it("uses conservative verdict language and preserves raw codes", () => {
    expect(localizedCode("POTENTIAL_MATCH")).toBe("잠재적 적합 · POTENTIAL_MATCH");
    expect(localizedCode("PRE_SCREEN_PASS")).toBe(
      "확인된 핵심 조건 충족 · PRE_SCREEN_PASS",
    );
    expect(localizedCode("REVIEW_REQUIRED")).toBe("전문가 검토 필요 · REVIEW_REQUIRED");
    expect(localizedCode("UNKNOWN")).toBe("근거 부족 · UNKNOWN");
    expect(localizedCode("CONFLICT")).toBe("근거 충돌 · CONFLICT");
    expect(verdictLabels.UNKNOWN).not.toContain("부적합");
  });

  it("keeps the exact seven backend stage keys", () => {
    expect(Object.keys(stageLabels)).toEqual([
      "Patient Evidence",
      "Trial Retrieval",
      "Protocol Compilation",
      "Eligibility Proof",
      "Proof Verification",
      "Ranking",
      "Next Question Optimization",
    ]);
  });

  it("does not translate unknown API codes", () => {
    expect(localizedCode("OPAQUE_REVIEW_REQUIRED")).toBe("OPAQUE_REVIEW_REQUIRED");
  });

  it("localizes question values without changing their API value", () => {
    expect(localizedQuestionValue("true")).toBe("기존 기록에서 확인됨");
    expect(localizedQuestionValue("false")).toBe("기존 기록에서 확인되지 않음");
    expect(localizedQuestionValue("server_defined_value")).toBe("server_defined_value");
  });
});

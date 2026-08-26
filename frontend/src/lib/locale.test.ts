import { describe, expect, it } from "vitest";

import { localizedCode, stageLabels, verdictLabels } from "./locale";

describe("Korean display mapping", () => {
  it("uses conservative verdict language and preserves raw codes", () => {
    expect(localizedCode("POTENTIAL_MATCH")).toBe("잠재적 적합 · POTENTIAL_MATCH");
    expect(localizedCode("UNKNOWN")).toBe("확인 필요 · UNKNOWN");
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
});

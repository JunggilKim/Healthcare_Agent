import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RetrievalCandidates } from "./RetrievalCandidates";

describe("RetrievalCandidates", () => {
  it("reports the actual retained and selected candidate counts", () => {
    render(
      <RetrievalCandidates
        retrieval={{
          mode: "hybrid_degraded",
          api_version: "2.0.5",
          registry_data_timestamp: "2026-08-27T00:00:00Z",
          dense_source_used: false,
          degradation_codes: [],
          selected_for_compilation: ["NCT1", "NCT2", "NCT3", "NCT4"],
          ranked_candidates: [],
        }}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "0 retained candidates · top 4 selected" }),
    ).toHaveTextContent("검색 후보 0건 · 상위 4건 상세 평가");
  });
});

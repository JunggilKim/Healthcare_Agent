import cytoscape from "cytoscape";
import { useEffect, useRef } from "react";

import type { SessionView } from "../types/api";

export function ProofGraph({ session }: { session: SessionView }) {
  const container = useRef<HTMLDivElement>(null);
  const proof =
    session.proofs.find((item) => item.missing_slot_ids.includes("pathology.histology")) ??
    session.proofs[0];

  useEffect(() => {
    if (!container.current || !proof || !session.trial_evaluation) return;
    const evidenceLabel = proof.evidence_fact_ids.length
      ? `${proof.evidence_fact_ids.length} admissible fact`
      : proof.missing_slot_ids[0] ?? "no admissible evidence";
    const graph = cytoscape({
      container: container.current,
      elements: [
        { data: { id: "source", label: "Patient source span" } },
        { data: { id: "fact", label: evidenceLabel } },
        { data: { id: "criterion", label: "Eligibility criterion" } },
        { data: { id: "verdict", label: proof.final_verdict } },
        { data: { id: "decision", label: session.trial_evaluation.decision } },
        { data: { id: "rank", label: "Rank #1" } },
        { data: { source: "source", target: "fact", label: "EXTRACTED_FROM" } },
        { data: { source: "fact", target: "criterion", label: "SUPPORTS" } },
        { data: { source: "criterion", target: "verdict", label: "EVALUATES" } },
        { data: { source: "verdict", target: "decision", label: "AGGREGATES_TO" } },
        { data: { source: "decision", target: "rank", label: "CONTRIBUTES_TO_RANK" } },
      ],
      layout: { name: "breadthfirst", directed: true, padding: 24, spacingFactor: 1.15 },
      style: [
        {
          selector: "node",
          style: {
            "background-color": "#13a39a",
            "border-color": "#55c7e8",
            "border-width": 2,
            color: "#ffffff",
            label: "data(label)",
            "font-size": 10,
            "text-wrap": "wrap",
            "text-max-width": "95px",
            "text-background-color": "#16304f",
            "text-background-opacity": .95,
            "text-background-padding": "4px",
            "text-background-shape": "roundrectangle",
            "text-valign": "bottom",
            "text-margin-y": 10,
            width: 32,
            height: 32,
          },
        },
        {
          selector: "edge",
          style: {
            width: 1.5,
            "line-color": "#647d99",
            "target-arrow-color": "#647d99",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
          },
        },
      ],
      userZoomingEnabled: false,
      userPanningEnabled: false,
      boxSelectionEnabled: false,
    });
    return () => graph.destroy();
  }, [proof, session.trial_evaluation]);

  return (
    <section className="panel proof-graph" aria-labelledby="proof-graph-title">
      <p className="eyebrow">필터된 근거 경로 · FILTERED PROOF PATH</p>
      <h2 id="proof-graph-title" className="panel-title">Proof graph · 증명 연결</h2>
      <p className="mt-2 text-xs leading-5 text-slate-500">선택 기준의 근거 경로만 표시합니다. 전체 세션 그래프는 렌더링하지 않습니다.</p>
      <div ref={container} className="mt-3 h-56 rounded-xl bg-slate-950/70" />
    </section>
  );
}

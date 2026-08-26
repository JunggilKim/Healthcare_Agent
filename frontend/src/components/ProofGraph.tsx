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
      ? `연결 근거 ${proof.evidence_fact_ids.length}개`
      : "연결 근거 없음";
    const graph = cytoscape({
      container: container.current,
      elements: [
        { data: { id: "source", label: "환자 설명 원문" }, position: { x: 80, y: 80 } },
        { data: { id: "fact", label: evidenceLabel }, position: { x: 230, y: 80 } },
        { data: { id: "criterion", label: "임상시험 선정 조건" }, position: { x: 380, y: 80 } },
        { data: { id: "verdict", label: proof.final_verdict }, position: { x: 530, y: 80 } },
        { data: { id: "decision", label: session.trial_evaluation.decision }, position: { x: 680, y: 80 } },
        { data: { id: "rank", label: "우선 검토 1순위" }, position: { x: 830, y: 80 } },
        { data: { source: "source", target: "fact", label: "EXTRACTED_FROM" } },
        { data: { source: "fact", target: "criterion", label: "SUPPORTS" } },
        { data: { source: "criterion", target: "verdict", label: "EVALUATES" } },
        { data: { source: "verdict", target: "decision", label: "AGGREGATES_TO" } },
        { data: { source: "decision", target: "rank", label: "CONTRIBUTES_TO_RANK" } },
      ],
      layout: { name: "preset", fit: true, padding: 42 },
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
      <p className="eyebrow">판정 근거 연결</p>
      <h2 id="proof-graph-title" className="panel-title">환자 기록이 판정에 연결되는 과정</h2>
      <p className="mt-2 text-xs leading-5 text-slate-500">현재 선택한 조건과 관련된 근거 경로만 간결하게 표시합니다.</p>
      <div ref={container} className="mt-3 h-56 rounded-xl bg-slate-950/70" />
    </section>
  );
}

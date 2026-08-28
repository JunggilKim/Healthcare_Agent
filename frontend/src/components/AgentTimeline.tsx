import { stageLabels, stageStateLabels } from "../lib/locale";

const stages = [
  "Patient Evidence",
  "Trial Retrieval",
  "Protocol Compilation",
  "Eligibility Proof",
  "Proof Verification",
  "Ranking",
  "Next Question Optimization",
];

export type StageState = "pending" | "running" | "completed" | "degraded" | "failed" | "skipped";

export function AgentTimeline({ states }: { states: Record<string, StageState> }) {
  return (
    <section
      aria-labelledby="agent-timeline-title"
      className="panel timeline-panel shrink-0"
      tabIndex={0}
    >
      <p className="eyebrow">AGENT PIPELINE · 7단계 분석</p>
      <h2 id="agent-timeline-title" className="panel-title">근거 기반 사전 선별 진행 상황</h2>
      <ol className="stage-list">
        {stages.map((stage, index) => {
          const state = states[stage] ?? "pending";
          const label = stageLabels[stage];
          return (
          <li key={stage} title={label.en} className={`stage-item stage-item-${state}`}>
            <span className={`stage-symbol stage-symbol-${state}`} aria-hidden="true">
              {state === "completed" ? "✓" : state === "failed" ? "!" : state === "degraded" ? "△" : state === "skipped" ? "—" : index + 1}
            </span>
            <span className="stage-copy">
              <strong>{label.ko}</strong>
            </span>
            <span className="stage-state">{stageStateLabels[state]}</span>
          </li>
        )})}
      </ol>
    </section>
  );
}

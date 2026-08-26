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

export type StageState = "pending" | "running" | "completed" | "degraded" | "failed";

export function AgentTimeline({ states }: { states: Record<string, StageState> }) {
  return (
    <section
      aria-labelledby="agent-timeline-title"
      className="panel timeline-panel shrink-0"
      tabIndex={0}
    >
      <p className="eyebrow">ROLE-SEPARATED PIPELINE</p>
      <h2 id="agent-timeline-title" className="panel-title">7단계 Agent Timeline</h2>
      <ol className="stage-list">
        {stages.map((stage, index) => {
          const state = states[stage] ?? "pending";
          const label = stageLabels[stage];
          return (
          <li key={stage} className={`stage-item stage-item-${state}`}>
            <span className={`stage-symbol stage-symbol-${state}`} aria-hidden="true">
              {state === "completed" ? "✓" : state === "failed" ? "!" : state === "degraded" ? "△" : index + 1}
            </span>
            <span className="stage-copy">
              <strong>{label.ko}</strong>
              <small>{label.en}</small>
            </span>
            <span className="stage-state">{stageStateLabels[state]}</span>
          </li>
        )})}
      </ol>
    </section>
  );
}

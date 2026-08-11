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
    <section aria-labelledby="agent-timeline-title" className="panel shrink-0 p-2">
      <p className="eyebrow">ROLE-SEPARATED PIPELINE</p>
      <h2 id="agent-timeline-title" className="mt-0.5 text-sm font-bold">Agent timeline</h2>
      <ol className="mt-1 space-y-0.5">
        {stages.map((stage, index) => (
          <li key={stage} className="flex items-center gap-2 text-[0.68rem] leading-4 text-slate-300">
            <span
              className={`stage-dot stage-${states[stage] ?? "pending"}`}
              aria-hidden="true"
            />
            <span>{index + 1}. {stage}</span>
            <span className="ml-auto text-[0.68rem] text-slate-500">
              {{ pending: "대기", running: "진행 중", completed: "완료", degraded: "대체 경로", failed: "실패" }[states[stage] ?? "pending"]}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

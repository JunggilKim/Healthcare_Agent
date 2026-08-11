const stages = [
  "Patient Evidence",
  "Trial Retrieval",
  "Protocol Compilation",
  "Eligibility Proof",
  "Proof Verification",
  "Ranking",
  "Next Question Optimization",
];

export function AgentTimeline({ complete }: { complete: boolean }) {
  return (
    <section aria-labelledby="agent-timeline-title" className="panel">
      <p className="eyebrow">ROLE-SEPARATED PIPELINE</p>
      <h2 id="agent-timeline-title" className="panel-title">Agent timeline</h2>
      <ol className="mt-5 space-y-3">
        {stages.map((stage, index) => (
          <li key={stage} className="flex items-center gap-3 text-sm text-slate-300">
            <span className={`stage-dot ${complete ? "stage-complete" : index === 0 ? "stage-running" : ""}`} aria-hidden="true" />
            <span>{index + 1}. {stage}</span>
            <span className="ml-auto text-xs text-slate-500">{complete ? "완료" : index === 0 ? "진행 중" : "대기"}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}


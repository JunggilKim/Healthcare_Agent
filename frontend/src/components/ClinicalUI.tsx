import type { ReactNode } from "react";

import { localizedCode, verdictLabels } from "../lib/locale";

export function StatusBadge({ code, className = "" }: { code: string; className?: string }) {
  const label = verdictLabels[code];
  return (
    <span
      aria-label={localizedCode(code)}
      className={`status-badge status-${code.toLowerCase().replaceAll("_", "-")} ${className}`}
    >
      {label ? <><span>{label}</span><span aria-hidden="true"> · </span></> : null}
      <span className="status-code">{code}</span>
    </span>
  );
}

export function SectionHeading({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="section-heading">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h2 className="panel-title">{title}</h2>
        {description ? <p className="section-description">{description}</p> : null}
      </div>
      {action ? <div className="section-action">{action}</div> : null}
    </div>
  );
}

export function SourceOriginal({
  label = "영어 원문 보기",
  children,
}: {
  label?: string;
  children: ReactNode;
}) {
  return (
    <details className="source-original">
      <summary>{label}</summary>
      <div className="source-original-content">{children}</div>
    </details>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <section className="panel empty-state" role="status">
      <span aria-hidden="true" className="state-symbol">∅</span>
      <div>
        <h2>표시할 근거가 없습니다.</h2>
        <p>{children}</p>
      </div>
    </section>
  );
}

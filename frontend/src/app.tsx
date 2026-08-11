import { useState } from "react";

import {
  analyzeSession,
  createS004Session,
  readSession,
  replayProof,
  submitPinnedAnswer,
  type SessionCredentials,
} from "./api/client";
import { AgentTimeline } from "./components/AgentTimeline";
import { CriterionMatrix } from "./components/CriterionMatrix";
import { Disclaimer } from "./components/Disclaimer";
import { QuestionPanel } from "./components/QuestionPanel";
import { TrialCard } from "./components/TrialCard";
import type { SessionView } from "./types/api";

export function App() {
  const [credentials, setCredentials] = useState<SessionCredentials | null>(null);
  const [session, setSession] = useState<SessionView | null>(null);
  const [busy, setBusy] = useState(false);
  const [pipelineComplete, setPipelineComplete] = useState(false);
  const [statusText, setStatusText] = useState("Snapshot Demo 준비됨");
  const [replayStatus, setReplayStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setBusy(true);
    setError(null);
    setStatusText("역할별 에이전트 파이프라인 실행 중…");
    try {
      const nextCredentials = await createS004Session();
      setCredentials(nextCredentials);
      await analyzeSession(nextCredentials, ({ event }) => setStatusText(`Event · ${event}`));
      setSession(await readSession(nextCredentials));
      setPipelineComplete(true);
      setStatusText("첫 번째 근거 획득 행동 선택 완료");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "분석을 완료하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function answer(branch: "A" | "B") {
    if (!credentials || !session?.current_question?.selected) return;
    setBusy(true);
    setReplayStatus(null);
    setStatusText("선택한 슬롯만 해석하고 증명을 다시 실행 중…");
    try {
      await submitPinnedAnswer(
        credentials,
        session.current_question.selected.question_id,
        branch,
        ({ event }) => setStatusText(`Reevaluation · ${event}`),
      );
      setSession(await readSession(credentials));
      setStatusText(
        branch === "A"
          ? "Histology PASS · Muscle invasion remains UNKNOWN"
          : "Histology unavailable · 동일 질문을 다시 묻지 않음",
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "재평가를 완료하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function replay() {
    if (!credentials) return;
    setReplayStatus("결정론적 proof replay 실행 중…");
    const passed = await replayProof(credentials);
    setReplayStatus(passed ? "Proof replay passed · PV-012 7/7" : "Proof replay failed");
  }

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 bg-slate-950/90 px-6 py-4 backdrop-blur">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-lg font-black tracking-tight">TRIAL-OPT</p>
            <p className="text-xs text-slate-500">Proof-carrying active evidence acquisition</p>
          </div>
          <div className="flex items-center gap-2">
            <span className="mode-badge">SNAPSHOT DEMO</span>
            <span className="mode-badge">DATA · 2026-08-11</span>
          </div>
        </div>
      </header>

      {!session ? (
        <section className="mx-auto grid max-w-6xl gap-8 px-6 py-14 lg:grid-cols-[1.15fr_0.85fr]">
          <div className="flex flex-col justify-center">
            <p className="eyebrow">2026 HEALTHCARE AGENTIC AI CHALLENGE</p>
            <h1 className="mt-5 text-5xl font-black leading-[1.08] tracking-tight sm:text-6xl">
              추측하지 않고,
              <br />확인할 근거를 선택합니다.
            </h1>
            <p className="mt-6 max-w-2xl text-lg leading-8 text-slate-300">
              검색 가설과 임상시험 판정 근거를 분리하고, 기준별 replayable proof를 만든 뒤 가장
              의사결정 가치가 큰 한 가지 정보만 요청합니다.
            </p>
            <div className="mt-8"><Disclaimer /></div>
          </div>
          <aside className="panel self-center border-cyan-400/30 p-7">
            <div className="flex items-center justify-between"><span className="rank-badge">S004</span><span className="mode-badge">FULL PHASE-1 SNAPSHOT</span></div>
            <h2 className="mt-5 text-2xl font-bold">Bladder wall mass · Evidence firewall</h2>
            <p className="mt-4 leading-7 text-slate-300">A 68-year-old man with a long smoking history presents with painless gross hematuria. CT urography reveals a mass in the bladder wall.</p>
            <div className="mt-5 rounded-xl border border-amber-300/30 bg-amber-100/10 p-4 text-sm leading-6 text-amber-100">영상상 종괴는 검색 가설을 만들 수 있지만 병리 확정 요로상피암 근거로 사용할 수 없습니다.</div>
            <button className="primary-button mt-6 w-full" disabled={busy} onClick={() => void start()}>{busy ? "분석 중…" : "S004 Snapshot 분석 시작"}</button>
            <p aria-live="polite" className="mt-3 text-center text-xs text-slate-500">{statusText}</p>
            {error ? <p role="alert" className="mt-3 text-sm text-rose-300">{error}</p> : null}
          </aside>
        </section>
      ) : (
        <div className="mx-auto max-w-[1500px] px-5 py-6">
          <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-800 bg-slate-900/70 px-5 py-3">
            <p className="text-sm text-slate-300">{statusText}</p>
            <button className="secondary-button py-2" onClick={() => void replay()}>Replay Proof</button>
          </div>
          {replayStatus ? <p aria-live="polite" className="mb-5 rounded-xl bg-emerald-300/10 p-3 text-sm text-emerald-200">{replayStatus}</p> : null}
          <div className="grid gap-5 xl:grid-cols-[280px_minmax(0,1fr)_390px]">
            <div className="space-y-5"><AgentTimeline complete={pipelineComplete} /><QuestionPanel session={session} busy={busy} onAnswer={(branch) => void answer(branch)} /></div>
            <div className="space-y-5"><TrialCard session={session} /><section className="panel"><p className="eyebrow">PATIENT SOURCE</p><p className="mt-3 leading-7 text-slate-300">{session.patient_text}</p></section></div>
            <div className="space-y-5"><section className="panel border-amber-300/30"><p className="eyebrow text-amber-300">EVIDENCE FIREWALL</p><h2 className="mt-3 font-bold">Imaging suspicion ≠ pathology confirmation</h2><p className="mt-3 text-sm leading-6 text-slate-400">Bladder cancer remains a Grade-H retrieval hypothesis. PV-007 verifies that no hypothesis ID appears in a hard decision.</p></section><CriterionMatrix session={session} /></div>
          </div>
          <div className="mt-6"><Disclaimer /></div>
        </div>
      )}
    </main>
  );
}


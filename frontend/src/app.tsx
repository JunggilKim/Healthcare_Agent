import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import {
  analyzeSession,
  createSession,
  deleteSession,
  exportReport,
  readDemoCases,
  readPublicConfig,
  readS004Retrieval,
  readSession,
  replayProof,
  resetSession,
  submitAnswer,
  type SessionCredentials,
} from "./api/client";
import { AgentTimeline, type StageState } from "./components/AgentTimeline";
import { CriterionMatrix } from "./components/CriterionMatrix";
import { Disclaimer } from "./components/Disclaimer";
import { QuestionPanel } from "./components/QuestionPanel";
import { ResearcherView } from "./components/ResearcherView";
import { RetrievalCandidates } from "./components/RetrievalCandidates";
import { TrialCard } from "./components/TrialCard";
import { retrievalSchema, type RetrievalView, type SessionView } from "./types/api";

const ProofGraph = lazy(() =>
  import("./components/ProofGraph").then((module) => ({ default: module.ProofGraph })),
);
const ExperimentEvidence = lazy(() =>
  import("./components/ExperimentEvidence").then((module) => ({
    default: module.ExperimentEvidence,
  })),
);

const stageNames = [
  "Patient Evidence",
  "Trial Retrieval",
  "Protocol Compilation",
  "Eligibility Proof",
  "Proof Verification",
  "Ranking",
  "Next Question Optimization",
] as const;

const eventStage: Record<string, (typeof stageNames)[number]> = {
  fact_extracted: "Patient Evidence",
  retrieval_completed: "Trial Retrieval",
  trial_compiled: "Protocol Compilation",
  trial_evaluated: "Eligibility Proof",
  proof_verified: "Proof Verification",
  rankings_updated: "Ranking",
  question_selected: "Next Question Optimization",
};

function initialStages(): Record<string, StageState> {
  return Object.fromEntries(stageNames.map((stage) => [stage, "pending"]));
}

function AboutPage() {
  return (
    <main className="min-h-screen bg-slate-950 px-6 py-16 text-slate-100">
      <article className="mx-auto max-w-3xl">
        <Link className="text-sm font-bold text-cyan-300" to="/">← Demo로 돌아가기</Link>
        <p className="eyebrow mt-12">ABOUT THE RESEARCH PROTOTYPE</p>
        <h1 className="mt-4 text-4xl font-black">TRIAL-OPT</h1>
        <p className="mt-6 text-lg leading-8 text-slate-300">불완전한 환자 설명을 진단으로 채우지 않고, 임상시험 판정을 바꿀 가능성이 큰 기존 근거 하나를 결정론적으로 선택하는 연구 프로토타입입니다.</p>
        <div className="mt-8"><Disclaimer /></div>
      </article>
    </main>
  );
}

export function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const [credentials, setCredentials] = useState<SessionCredentials | null>(() => {
    const match = location.pathname.match(/^\/session\/([^/]+)$/);
    if (!match) return null;
    const token = sessionStorage.getItem(`trial-opt:${match[1]}`);
    return token ? { sessionId: match[1], token } : null;
  });
  const [session, setSession] = useState<SessionView | null>(null);
  const [retrieval, setRetrieval] = useState<RetrievalView | null>(null);
  const [busy, setBusy] = useState(false);
  const [statusText, setStatusText] = useState("Snapshot Demo 준비됨");
  const [replayStatus, setReplayStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedCase, setSelectedCase] = useState("S004");
  const [mode, setMode] = useState<"snapshot" | "live">("snapshot");
  const [inputMode, setInputMode] = useState<"seed" | "text">("seed");
  const [patientText, setPatientText] = useState("");
  const [confirmedSynthetic, setConfirmedSynthetic] = useState(false);
  const [identifierAcknowledged, setIdentifierAcknowledged] = useState(false);
  const [evaluationDate, setEvaluationDate] = useState("2026-08-11");
  const [tab, setTab] = useState<"patient" | "research" | "experiment">("patient");
  const [stages, setStages] = useState<Record<string, StageState>>(initialStages);
  const [degradationCodes, setDegradationCodes] = useState<string[]>([]);
  const casesQuery = useQuery({ queryKey: ["demo-cases"], queryFn: readDemoCases });
  const configQuery = useQuery({ queryKey: ["public-config"], queryFn: readPublicConfig });

  const identifierRanges = useMemo(() => {
    const patterns = [
      ["EMAIL", /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi],
      ["PHONE", /(?:\+?\d[\d .()-]{7,}\d)/g],
      ["EXPLICIT LABEL", /\b(?:name|patient\s*id|mrn)\s*:|(?:주민등록번호|환자번호)\s*:/gi],
    ] as const;
    return patterns.flatMap(([category, pattern]) =>
      [...patientText.matchAll(pattern)].map((match) => ({
        category,
        start: match.index ?? 0,
        end: (match.index ?? 0) + match[0].length,
      })),
    );
  }, [patientText]);

  const showDemoTools = new URLSearchParams(location.search).get("demo-tools") === "1";
  const selectedCaseRecord = casesQuery.data?.find((item) => item.id === selectedCase);
  const canStart =
    !busy &&
    Boolean(evaluationDate) &&
    (mode === "snapshot"
      ? inputMode === "seed" && Boolean(selectedCaseRecord?.has_full_snapshot)
      : Boolean(configQuery.data?.live_available) &&
        (inputMode === "seed" ||
          (Boolean(patientText.trim()) &&
            confirmedSynthetic &&
            (identifierRanges.length === 0 || identifierAcknowledged))));

  useEffect(() => {
    if (!credentials || session) return;
    void readSession(credentials)
      .then((restored) => {
        setSession(restored);
        const parsed = retrievalSchema.safeParse(restored.retrieval);
        if (parsed.success) setRetrieval(parsed.data);
        else if (restored.top_trial?.nct_id === "NCT05239624") {
          void readS004Retrieval().then(setRetrieval).catch(() => undefined);
        }
      })
      .catch(() => setError("세션을 복구하지 못했습니다."));
  }, [credentials, session]);

  function updateStage(event: string) {
    const current = eventStage[event];
    if (!current) return;
    setStages((previous) => {
      const next = { ...previous };
      const currentIndex = stageNames.indexOf(current);
      stageNames.forEach((stage, index) => {
        if (index <= currentIndex) next[stage] = "completed";
        else if (index === currentIndex + 1) next[stage] = "running";
      });
      return next;
    });
  }

  async function start() {
    if (!canStart) return;
    setBusy(true);
    setError(null);
    setStages({ ...initialStages(), "Patient Evidence": "running" });
    setStatusText("역할별 에이전트 파이프라인 실행 중…");
    try {
      const nextCredentials = await createSession({
        mode,
        seedCaseId: inputMode === "seed" ? selectedCase : undefined,
        patientText: inputMode === "text" ? patientText : undefined,
        evaluationDate,
        language: "auto",
        confirmSyntheticPublic: inputMode === "text" && confirmedSynthetic,
        identifierWarningAcknowledged:
          inputMode === "text" && identifierRanges.length > 0 && identifierAcknowledged,
      });
      setCredentials(nextCredentials);
      if (mode === "snapshot" && selectedCase === "S004") {
        setRetrieval(await readS004Retrieval());
      }
      await analyzeSession(nextCredentials, ({ event }) => {
        updateStage(event);
        setStatusText(`Pipeline event · ${event}`);
      });
      const nextSession = await readSession(nextCredentials);
      setSession(nextSession);
      const parsedRetrieval = retrievalSchema.safeParse(nextSession.retrieval);
      if (parsedRetrieval.success) setRetrieval(parsedRetrieval.data);
      setStages(Object.fromEntries(stageNames.map((stage) => [stage, "completed"])));
      setStatusText("첫 번째 근거 획득 행동 선택 완료");
      void navigate(
        `/session/${nextCredentials.sessionId}${showDemoTools ? "?demo-tools=1" : ""}`,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "분석을 완료하지 못했습니다.");
      setStages((previous) => ({ ...previous, "Patient Evidence": "failed" }));
    } finally {
      setBusy(false);
    }
  }

  async function answer(input: {
    answerText?: string;
    unknown?: boolean;
    declined?: boolean;
  }) {
    if (!credentials || !session?.current_question?.selected) return;
    setBusy(true);
    setReplayStatus(null);
    setStatusText("선택한 슬롯만 해석하고 증명을 다시 실행 중…");
    try {
      await submitAnswer(
        credentials,
        session.current_question.selected.question_id,
        input,
        ({ event }) => {
          updateStage(event);
          setStatusText(`Reevaluation · ${event}`);
        },
      );
      setSession(await readSession(credentials));
      setStatusText(
        input.unknown || input.declined
          ? `${session.current_question.selected.slot_id} unavailable · 동일 질문을 다시 묻지 않음`
          : `${session.current_question.selected.slot_id} 재평가 완료`,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "재평가를 완료하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function replay() {
    if (!credentials) return;
    const started = performance.now();
    setReplayStatus("결정론적 proof replay 실행 중…");
    const nctId = session?.top_trial?.nct_id ?? session?.trial_evaluation?.nct_id;
    if (!nctId) {
      setReplayStatus("Replay할 상위 trial proof가 없습니다.");
      return;
    }
    const result = await replayProof(credentials, nctId);
    const elapsed = Math.round(performance.now() - started);
    setReplayStatus(
      result.passed
        ? `Proof replay passed · PV-012 ${result.packetCount}/${result.packetCount} · ${elapsed} ms`
        : "Proof replay failed",
    );
  }

  async function resetCurrentSession() {
    if (!credentials) return;
    setBusy(true);
    setError(null);
    try {
      const nextCredentials = await resetSession(credentials);
      setCredentials(nextCredentials);
      setSession(null);
      setStages({ ...initialStages(), "Patient Evidence": "running" });
      await analyzeSession(nextCredentials, ({ event }) => updateStage(event));
      setSession(await readSession(nextCredentials));
      void navigate(`/session/${nextCredentials.sessionId}`);
      setStatusText("새 세션으로 근거 상태를 초기화했습니다.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "세션을 초기화하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function deleteCurrentSession() {
    if (!credentials) return;
    if (!window.confirm("현재 세션과 생성된 세션 아티팩트를 삭제할까요?")) return;
    setBusy(true);
    setError(null);
    try {
      await deleteSession(credentials);
      setCredentials(null);
      setSession(null);
      setRetrieval(null);
      setStages(initialStages());
      void navigate("/");
      setStatusText("세션을 삭제했습니다.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "세션을 삭제하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  function toggleFailure(code: string) {
    setDegradationCodes((current) =>
      current.includes(code) ? current.filter((item) => item !== code) : [...current, code],
    );
    const stage = code.includes("CTGOV")
      ? "Trial Retrieval"
      : code.includes("EMBEDDING")
        ? "Trial Retrieval"
        : "Protocol Compilation";
    setStages((current) => ({ ...current, [stage]: "degraded" }));
  }

  if (location.pathname === "/about") return <AboutPage />;

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <header className="sticky top-0 z-20 border-b border-slate-800 bg-slate-950/95 px-6 py-2 backdrop-blur">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center justify-between gap-4">
          <Link to="/" className="group">
            <p className="text-lg font-black tracking-tight group-hover:text-cyan-200">TRIAL-OPT</p>
            <p className="text-[0.68rem] text-slate-500">Proof-carrying active evidence acquisition</p>
          </Link>
          <div className="flex flex-wrap items-center gap-2">
            <span className="mode-badge">{session?.mode === "snapshot" || !session ? "SNAPSHOT DEMO" : session.mode.toUpperCase()}</span>
            <span className="mode-badge">DATA · 2026-08-11 09:00 UTC</span>
            <span className="mode-badge">MODEL · CACHED / $0.000</span>
            {degradationCodes.length ? <span className="degraded-badge">DEGRADED · {degradationCodes.length}</span> : null}
            <Link className="secondary-button px-3 py-2" to="/about">About</Link>
          </div>
        </div>
      </header>

      {!session ? (
        <section className="mx-auto max-w-[1320px] px-6 py-10">
          <div className="grid gap-8 xl:grid-cols-[0.82fr_1.18fr]">
            <div className="flex flex-col justify-center">
              <p className="eyebrow">2026 HEALTHCARE AGENTIC AI CHALLENGE</p>
              <h1 className="mt-4 text-5xl font-black leading-[1.04] tracking-tight sm:text-6xl">추측하지 않고,<br />확인할 근거를 선택합니다.</h1>
              <p className="mt-5 max-w-2xl text-base leading-7 text-slate-300">검색 가설과 자격 판정 근거를 분리하고, 재생 가능한 criterion proof를 만든 뒤 결정 가치가 가장 큰 기존 정보 한 가지만 요청합니다.</p>
              <div className="mt-6"><Disclaimer /></div>
            </div>

            <section className="panel border-cyan-400/30 p-6" aria-labelledby="input-title">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div><p className="eyebrow">START A PRE-SCREEN</p><h2 id="input-title" className="panel-title">Synthetic/public input only</h2></div>
                <div className="flex rounded-xl bg-slate-950 p-1">
                  {(["snapshot", "live"] as const).map((item) => <button key={item} onClick={() => setMode(item)} className={`segmented ${mode === item ? "segmented-active" : ""}`}>{item === "snapshot" ? "Snapshot Demo" : "Live Mode"}</button>)}
                </div>
              </div>
              {mode === "live" ? <p className="mt-3 rounded-xl border border-amber-300/30 bg-amber-100/5 p-3 text-sm text-amber-100">{configQuery.data?.live_available ? "Live Mode 활성화됨 · first-party Google Cloud ADC와 비용 guard를 사용합니다." : "Live Mode는 Google Cloud ADC·결제·quota 외부 검증 전까지 비활성입니다. Snapshot은 계속 사용할 수 있습니다."}</p> : null}
              <div className="mt-5 flex gap-2 border-b border-slate-800 pb-3">
                <button className={`tab-button ${inputMode === "seed" ? "tab-active" : ""}`} onClick={() => setInputMode("seed")}>Organizer seed</button>
                <button className={`tab-button ${inputMode === "text" ? "tab-active" : ""}`} onClick={() => setInputMode("text")}>Free text</button>
              </div>
              {inputMode === "seed" ? (
                <div className="mt-4 grid max-h-64 gap-2 overflow-y-auto pr-1 sm:grid-cols-2">
                  {(casesQuery.data ?? []).map((item) => (
                    <button key={item.id} onClick={() => setSelectedCase(item.id)} className={`seed-card ${selectedCase === item.id ? "seed-card-active" : ""}`}>
                      <span className="flex items-center justify-between"><strong>{item.id}</strong><span className={item.has_full_snapshot ? "text-emerald-300" : "text-slate-500"}>{item.has_full_snapshot ? "FULL" : "DOMAIN ONLY"}</span></span>
                      <span className="mt-2 line-clamp-2 text-left text-xs leading-5 text-slate-400">{item.text}</span>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="mt-4">
                  <label className="text-sm font-bold" htmlFor="patient-text">Public or synthetic patient description</label>
                  <textarea id="patient-text" className="mt-2 min-h-32 w-full rounded-xl border border-slate-700 bg-slate-950 p-3 text-sm" maxLength={12000} value={patientText} onChange={(event) => { setPatientText(event.target.value); setIdentifierAcknowledged(false); }} />
                  <label className="mt-3 flex items-start gap-3 text-sm leading-6 text-slate-300"><input type="checkbox" className="mt-1" checked={confirmedSynthetic} onChange={(event) => setConfirmedSynthetic(event.target.checked)} />이 입력은 공개 또는 합성 데이터이며 실제 환자 정보가 포함되지 않았습니다.</label>
                  {identifierRanges.length ? <div role="alertdialog" aria-label="잠재적 식별자 경고" className="mt-3 rounded-xl border border-rose-300/40 bg-rose-300/10 p-4"><p className="font-bold text-rose-200">잠재적 식별자 패턴을 확인하세요</p><ul className="mt-2 text-xs text-slate-300">{identifierRanges.map((item, index) => <li key={`${item.category}-${index}`}>{item.category} · characters {item.start}–{item.end}</li>)}</ul><label className="mt-3 flex gap-2 text-xs"><input type="checkbox" checked={identifierAcknowledged} onChange={(event) => setIdentifierAcknowledged(event.target.checked)} />합성 placeholder임을 다시 확인합니다.</label></div> : null}
                </div>
              )}
              <div className="mt-5 grid gap-3 sm:grid-cols-[1fr_auto]">
                <label className="text-xs font-bold text-slate-400">Evaluation date<input type="date" value={evaluationDate} onChange={(event) => setEvaluationDate(event.target.value)} className="mt-1 block w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white" /></label>
                <button className="primary-button self-end" disabled={!canStart} onClick={() => void start()}>{busy ? "분석 중…" : mode === "live" ? "Live 분석 시작" : selectedCaseRecord?.has_full_snapshot ? `${selectedCase} Snapshot 분석 시작` : "Full snapshot 준비 중"}</button>
              </div>
              <p aria-live="polite" className="mt-3 text-center text-xs text-slate-500">{statusText}</p>
              {error ? <p role="alert" className="mt-3 text-sm text-rose-300">{error}</p> : null}
            </section>
          </div>
        </section>
      ) : (
        <div className="mx-auto max-w-[1500px] px-5 py-3">
          {degradationCodes.length ? <div role="status" className="mb-4 rounded-xl border border-amber-300/40 bg-amber-100/10 px-4 py-3 text-sm text-amber-100">Partial results preserved · {degradationCodes.join(" · ")} · Snapshot/template fallback active</div> : null}
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-2">
            <p className="text-sm text-slate-300">{statusText}</p>
            <div className="flex flex-wrap gap-2"><button className="secondary-button py-2" onClick={() => void replay()}>Replay Proof</button><button className="secondary-button py-2" onClick={() => credentials && void exportReport(credentials)}>Export report</button><button className="secondary-button py-2" disabled={busy} onClick={() => void resetCurrentSession()}>Reset session</button><button className="secondary-button py-2 text-rose-200" disabled={busy} onClick={() => void deleteCurrentSession()}>Delete session</button></div>
          </div>
          {replayStatus ? <p aria-live="polite" className="mb-4 rounded-xl bg-emerald-300/10 p-3 text-sm text-emerald-200">{replayStatus}</p> : null}
          {showDemoTools ? <section className="mb-4 rounded-xl border border-dashed border-fuchsia-300/40 bg-fuchsia-300/5 p-3" aria-label="Failure simulation controls"><p className="text-xs font-bold text-fuchsia-200">REHEARSAL ONLY · FAILURE SIMULATION</p><div className="mt-2 flex flex-wrap gap-2">{["GEMINI_UNAVAILABLE", "CTGOV_UNAVAILABLE", "EMBEDDING_UNAVAILABLE"].map((code) => <button key={code} className="secondary-button px-3 py-2 text-xs" onClick={() => toggleFailure(code)}>{degradationCodes.includes(code) ? "✓ " : ""}{code}</button>)}</div></section> : null}

          <div className="workspace-grid workspace-primary">
            <div className="flex min-h-0 flex-col gap-3"><AgentTimeline states={stages} /><QuestionPanel session={session} busy={busy} onAnswer={(branch) => void answer(branch)} /></div>
            <div className="min-h-0 space-y-3 overflow-y-auto"><TrialCard session={session} /><section className="panel p-4"><p className="eyebrow">PATIENT SOURCE</p><p className="mt-2 text-sm leading-6 text-slate-300">{session.patient_text}</p></section></div>
            <div className="flex min-h-0 flex-col gap-3"><section className="panel border-amber-300/30 p-3"><p className="eyebrow text-amber-300">EVIDENCE FIREWALL</p><h2 className="mt-1 text-sm font-bold">Imaging suspicion ≠ pathology confirmation</h2><p className="mt-1 text-xs leading-4 text-slate-400">Bladder cancer remains a Grade-H retrieval hypothesis. PV-007 confirms no hypothesis enters a hard decision.</p></section><CriterionMatrix session={session} /></div>
          </div>

          <nav className="mt-3 flex gap-1 rounded-xl border border-slate-800 bg-slate-900/70 p-1" aria-label="Workspace evidence tabs">{(["patient", "research", "experiment"] as const).map((item) => <button key={item} onClick={() => setTab(item)} className={`workspace-tab ${tab === item ? "workspace-tab-active" : ""}`}>{item === "patient" ? "Patient Summary" : item === "research" ? "Researcher View" : "Experiment Evidence"}</button>)}</nav>
          <div className="mt-4"><Suspense fallback={<section className="panel text-sm text-slate-300">Evidence view loading…</section>}>{tab === "patient" ? <div className="grid gap-4 xl:grid-cols-[1fr_0.8fr]"><ProofGraph session={session} />{retrieval ? <RetrievalCandidates retrieval={retrieval} /> : null}</div> : tab === "research" ? <ResearcherView session={session} /> : <ExperimentEvidence />}</Suspense></div>
          <div className="mt-5"><Disclaimer /></div>
        </div>
      )}
    </main>
  );
}

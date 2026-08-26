import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
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
import { EmptyState } from "./components/ClinicalUI";
import { CriterionMatrix } from "./components/CriterionMatrix";
import { Disclaimer } from "./components/Disclaimer";
import { QuestionPanel } from "./components/QuestionPanel";
import { ResearcherView } from "./components/ResearcherView";
import { RetrievalCandidates } from "./components/RetrievalCandidates";
import { TrialCard } from "./components/TrialCard";
import { ko } from "./lib/locale";
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
    <main className="about-page min-h-screen px-6 py-16">
      <article className="about-card mx-auto max-w-3xl">
        <Link className="text-sm font-bold text-blue-700" to="/">← 데모로 돌아가기</Link>
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
  const restorePending = useRef(Boolean(credentials));
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
  const [liveStalled, setLiveStalled] = useState(false);
  const analysisAbort = useRef<AbortController | null>(null);
  const cancelledForFallback = useRef(false);
  const casesQuery = useQuery({ queryKey: ["demo-cases"], queryFn: readDemoCases });
  const configQuery = useQuery({ queryKey: ["public-config"], queryFn: readPublicConfig });

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "auto" });
  }, [location.pathname]);

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
    if (!restorePending.current || !credentials || session) return;
    restorePending.current = false;
    void readSession(credentials)
      .then((restored) => {
        setSession(restored);
        setDegradationCodes(restored.degradation_codes);
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
    setLiveStalled(false);
    setDegradationCodes([]);
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
      const controller = new AbortController();
      analysisAbort.current = controller;
      await analyzeSession(
        nextCredentials,
        ({ event, data }) => {
          updateStage(event);
          setLiveStalled(false);
          if (Array.isArray(data.degradation_codes)) {
            setDegradationCodes(
              data.degradation_codes.filter(
                (item): item is string => typeof item === "string",
              ),
            );
          }
          setStatusText(`Pipeline event · ${event}`);
        },
        {
          signal: controller.signal,
          onStall: () => {
            if (mode === "live") {
              setLiveStalled(true);
              setStatusText("Live 외부 단계에서 8초 동안 새 이벤트가 없습니다.");
            }
          },
        },
      );
      analysisAbort.current = null;
      const nextSession = await readSession(nextCredentials);
      setSession(nextSession);
      setDegradationCodes(nextSession.degradation_codes);
      const parsedRetrieval = retrievalSchema.safeParse(nextSession.retrieval);
      if (parsedRetrieval.success) setRetrieval(parsedRetrieval.data);
      setStages(Object.fromEntries(stageNames.map((stage) => [stage, "completed"])));
      setStatusText("첫 번째 근거 획득 행동 선택 완료");
      void navigate(
        `/session/${nextCredentials.sessionId}${showDemoTools ? "?demo-tools=1" : ""}`,
      );
    } catch (caught) {
      if (
        !(
          caught instanceof DOMException &&
          caught.name === "AbortError" &&
          cancelledForFallback.current
        )
      ) {
        setError(caught instanceof Error ? caught.message : "분석을 완료하지 못했습니다.");
        setStages((previous) => ({ ...previous, "Patient Evidence": "failed" }));
      }
    } finally {
      analysisAbort.current = null;
      cancelledForFallback.current = false;
      setBusy(false);
    }
  }

  function prepareSnapshotFallback() {
    if (analysisAbort.current) {
      cancelledForFallback.current = true;
      analysisAbort.current.abort();
    }
    setLiveStalled(false);
    setSession(null);
    setCredentials(null);
    setRetrieval(null);
    setDegradationCodes([]);
    setMode("snapshot");
    setInputMode("seed");
    setSelectedCase("S004");
    setStatusText("검증된 S004 Snapshot을 별도 세션으로 시작할 수 있습니다.");
    void navigate("/");
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
      const updatedSession = await readSession(credentials);
      setSession(updatedSession);
      setDegradationCodes(updatedSession.degradation_codes);
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
    <main className="app-shell min-h-screen" aria-busy={busy}>
      <header className="app-header sticky top-0 z-20">
        <div className="app-header-inner mx-auto flex max-w-[1500px] flex-wrap items-center justify-between gap-4">
          <Link to="/" className="brand-lockup group">
            <p className="brand-name">{ko.product.name}</p>
            <p className="brand-descriptor">{ko.product.descriptor}</p>
          </Link>
          <div className="header-meta flex flex-wrap items-center gap-2">
            <span className="mode-badge">{session?.mode === "snapshot" || !session ? ko.mode.snapshot : ko.mode.live}</span>
            <span className="mode-badge">데이터 · DATA 2026-08-11</span>
            <span className="mode-badge hidden sm:inline-flex">모델 · CACHED / $0.000</span>
            {degradationCodes.length ? <span className="degraded-badge">성능 저하 · DEGRADED {degradationCodes.length}</span> : null}
            <Link className="secondary-button px-3 py-2" to="/about">About</Link>
          </div>
        </div>
      </header>

      {!session ? (
        <section className="landing-shell mx-auto max-w-[1320px] px-6 py-10">
          <div className="landing-grid grid gap-8 xl:grid-cols-[0.96fr_1.04fr]">
            <div className="landing-hero flex flex-col justify-center">
              <p className="eyebrow">2026 HEALTHCARE AGENTIC AI CHALLENGE</p>
              <h1 className="mt-4 text-5xl font-black leading-[1.04] tracking-tight sm:text-6xl">추측하지 않고,<br />확인할 근거를 선택합니다.</h1>
              <p className="mt-5 max-w-2xl text-base leading-7 text-slate-300">검색 가설과 자격 판정 근거를 분리하고, 재생 가능한 criterion proof를 만든 뒤 결정 가치가 가장 큰 기존 정보 한 가지만 요청합니다.</p>
              <ol className="landing-principles" aria-label="TRIAL-OPT 데모 흐름">
                <li><span>01</span><div><strong>근거를 분리</strong><small>Retrieval hypothesis ≠ admissible evidence</small></div></li>
                <li><span>02</span><div><strong>증명을 검증</strong><small>Criterion Proof · Evidence Firewall · PV-012</small></div></li>
                <li><span>03</span><div><strong>다음 질문을 선택</strong><small>새 검사가 아닌 기존 기록 한 가지를 요청</small></div></li>
              </ol>
              <div className="mt-6"><Disclaimer /></div>
            </div>

            <section className="panel pre-screen-card" aria-labelledby="input-title">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div><p className="eyebrow">START A PRE-SCREEN</p><h2 id="input-title" className="panel-title">사전 선별 시작</h2><p className="section-description">공개 또는 합성 데이터만 사용</p></div>
                <div className="segmented-control flex rounded-xl p-1">
                  {(["snapshot", "live"] as const).map((item) => <button key={item} onClick={() => setMode(item)} className={`segmented ${mode === item ? "segmented-active" : ""}`}>{item === "snapshot" ? ko.mode.snapshot : ko.mode.live}</button>)}
                </div>
              </div>
              {mode === "live" ? <p className="runtime-banner runtime-warning mt-3">{configQuery.data?.live_available ? "라이브 모드 활성화 · first-party Google Cloud ADC와 비용 guard를 사용합니다." : "라이브 모드는 Google Cloud ADC·결제·quota 외부 검증 전까지 비활성입니다. 스냅샷 데모는 계속 사용할 수 있습니다."}</p> : null}
              <div className="input-tabs mt-5 flex gap-2 pb-3">
                <button className={`tab-button ${inputMode === "seed" ? "tab-active" : ""}`} onClick={() => setInputMode("seed")}>주최자 시드 · Organizer seed</button>
                <button className={`tab-button ${inputMode === "text" ? "tab-active" : ""}`} onClick={() => setInputMode("text")}>자유 입력 · Free text</button>
              </div>
              {inputMode === "seed" ? (
                <div className="mt-4 grid max-h-64 gap-2 overflow-y-auto pr-1 sm:grid-cols-2">
                  {(casesQuery.data ?? []).map((item) => (
                    <button key={item.id} onClick={() => setSelectedCase(item.id)} className={`seed-card ${selectedCase === item.id ? "seed-card-active" : ""}`}>
                      <span className="flex items-center justify-between"><strong>{item.id}</strong><span className={item.has_full_snapshot ? "text-emerald-300" : "text-slate-500"}>{item.has_full_snapshot ? "전체 스냅샷 · FULL" : "도메인 경로 · DOMAIN ONLY"}</span></span>
                      <span className="mt-2 line-clamp-2 text-left text-xs leading-5 text-slate-400">{item.text}</span>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="mt-4">
                  <label className="text-sm font-bold" htmlFor="patient-text">공개 또는 합성 환자 설명 · Public or synthetic patient description</label>
                  <textarea id="patient-text" className="clinical-input mt-2 min-h-32 w-full p-3 text-sm" maxLength={12000} value={patientText} onChange={(event) => { setPatientText(event.target.value); setIdentifierAcknowledged(false); }} />
                  <label className="mt-3 flex items-start gap-3 text-sm leading-6 text-slate-300"><input type="checkbox" className="mt-1" checked={confirmedSynthetic} onChange={(event) => setConfirmedSynthetic(event.target.checked)} />이 입력은 공개 또는 합성 데이터이며 실제 환자 정보가 포함되지 않았습니다.</label>
                  {identifierRanges.length ? <div role="alertdialog" aria-label="잠재적 식별자 경고" className="mt-3 rounded-xl border border-rose-300/40 bg-rose-300/10 p-4"><p className="font-bold text-rose-200">잠재적 식별자 패턴을 확인하세요</p><ul className="mt-2 text-xs text-slate-300">{identifierRanges.map((item, index) => <li key={`${item.category}-${index}`}>{item.category} · characters {item.start}–{item.end}</li>)}</ul><label className="mt-3 flex gap-2 text-xs"><input type="checkbox" checked={identifierAcknowledged} onChange={(event) => setIdentifierAcknowledged(event.target.checked)} />합성 placeholder임을 다시 확인합니다.</label></div> : null}
                </div>
              )}
              <div className="mt-5 grid gap-3 sm:grid-cols-[1fr_auto]">
                <label className="text-xs font-bold text-slate-400">평가 기준일 · Evaluation date<input type="date" value={evaluationDate} onChange={(event) => setEvaluationDate(event.target.value)} className="clinical-input mt-1 block w-full px-3 py-2 text-sm" /></label>
                <button className="primary-button self-end" disabled={!canStart} onClick={() => void start()}>{busy ? "분석 중…" : mode === "live" ? "Live 분석 시작" : selectedCaseRecord?.has_full_snapshot ? `${selectedCase} Snapshot 분석 시작` : "Full snapshot 준비 중"}</button>
              </div>
              <p aria-live="polite" className="mt-3 text-center text-xs text-slate-500">{statusText}</p>
              {liveStalled || (mode === "live" && busy && degradationCodes.length) ? <div role="status" className="runtime-banner runtime-warning mt-3"><p>{liveStalled ? "Live 단계가 지연되고 있습니다." : "Live 의존성 강등 이벤트를 받았습니다."} 임의 입력은 Snapshot에 자동 매핑하지 않습니다.</p><button className="secondary-button mt-2 py-2" onClick={prepareSnapshotFallback}>현재 요청을 중단하고 별도 S004 Snapshot 준비</button></div> : null}
              {error ? <p role="alert" className="mt-3 text-sm text-rose-300">{error}</p> : null}
            </section>
          </div>
        </section>
      ) : (
        <div className="workspace-shell mx-auto max-w-[1500px] px-5 py-4">
          {degradationCodes.length ? <div role="status" className="runtime-banner runtime-warning mb-4"><p><strong>부분 결과 보존 · Partial results preserved</strong> · {degradationCodes.join(" · ")} · Snapshot/template fallback active</p><button className="secondary-button mt-2 py-2" onClick={prepareSnapshotFallback}>별도 S004 Snapshot 시작</button></div> : null}
          <div className="workspace-toolbar mb-3 flex flex-wrap items-center justify-between gap-3 px-4 py-2">
            <p className="text-sm text-slate-300">{statusText}</p>
            <div className="flex flex-wrap gap-2"><button aria-label="Replay Proof" className="secondary-button py-2" onClick={() => void replay()}>{ko.action.replay}</button><button aria-label="Export report" className="secondary-button py-2" disabled={!session.export_available} title={session.export_available ? undefined : "Persistence degraded; durable export is unavailable."} onClick={() => credentials && void exportReport(credentials)}>{ko.action.export}</button><button aria-label="Reset session" className="secondary-button py-2" disabled={busy} onClick={() => void resetCurrentSession()}>{ko.action.reset}</button><button aria-label="Delete session" className="danger-button py-2" disabled={busy} onClick={() => void deleteCurrentSession()}>{ko.action.delete}</button></div>
          </div>
          {replayStatus ? <p aria-live="polite" className="mb-4 rounded-xl bg-emerald-300/10 p-3 text-sm text-emerald-200">{replayStatus}</p> : null}
          {showDemoTools ? <section className="mb-4 rounded-xl border border-dashed border-fuchsia-300/40 bg-fuchsia-300/5 p-3" aria-label="Failure simulation controls"><p className="text-xs font-bold text-fuchsia-200">REHEARSAL ONLY · FAILURE SIMULATION</p><div className="mt-2 flex flex-wrap gap-2">{["GEMINI_UNAVAILABLE", "CTGOV_UNAVAILABLE", "EMBEDDING_UNAVAILABLE"].map((code) => <button key={code} className="secondary-button px-3 py-2 text-xs" onClick={() => toggleFailure(code)}>{degradationCodes.includes(code) ? "✓ " : ""}{code}</button>)}</div></section> : null}

          <div className={`workspace-grid workspace-primary ${tab === "patient" ? "" : "workspace-primary-evidence"}`}>
            <div className="flex min-h-0 flex-col gap-3"><AgentTimeline states={stages} /><QuestionPanel session={session} busy={busy} onAnswer={(branch) => void answer(branch)} /></div>
            <div className="min-h-0 space-y-3 overflow-y-auto"><TrialCard session={session} /><section className="panel p-4"><p className="eyebrow">PATIENT SOURCE</p><p className="mt-2 text-sm leading-6 text-slate-300">{session.patient_text}</p></section></div>
            <div className="flex min-h-0 flex-col gap-3"><section className="panel firewall-panel"><p className="eyebrow">근거 방화벽 · EVIDENCE FIREWALL</p><h2>Imaging suspicion ≠ pathology confirmation</h2><p>방광암은 Grade-H 검색 가설로 유지됩니다. PV-007은 가설이 hard decision에 들어가지 않음을 검증합니다.</p></section><CriterionMatrix session={session} /></div>
          </div>

          <nav className="workspace-tabs mt-3 flex gap-1 p-1" aria-label="Workspace evidence tabs">{(["patient", "research", "experiment"] as const).map((item) => <button aria-label={item === "patient" ? "Patient Summary" : item === "research" ? "Researcher View" : "Experiment Evidence"} key={item} onClick={() => setTab(item)} className={`workspace-tab ${tab === item ? "workspace-tab-active" : ""}`}>{item === "patient" ? "환자·판정" : item === "research" ? "연구 근거" : "실험 근거"}<small>{item === "patient" ? "Patient Summary" : item === "research" ? "Researcher View" : "Experiment Evidence"}</small></button>)}</nav>
          <div className="evidence-view mt-4"><Suspense fallback={<section className="panel runtime-loading">근거 화면 불러오는 중 · Evidence view loading…</section>}>{tab === "patient" ? <div className="grid gap-4 xl:grid-cols-[1fr_0.8fr]"><ProofGraph session={session} />{retrieval ? <RetrievalCandidates retrieval={retrieval} /> : <EmptyState>검색 후보가 세션에 기록되지 않았습니다. 현재 판정과 부분 proof는 그대로 유지됩니다.</EmptyState>}</div> : tab === "research" ? <ResearcherView session={session} /> : <ExperimentEvidence />}</Suspense></div>
          <div className="mt-5"><Disclaimer /></div>
        </div>
      )}
    </main>
  );
}

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
import { EvidenceFirewall } from "./components/EvidenceFirewall";
import { QuestionPanel } from "./components/QuestionPanel";
import { ResearcherView } from "./components/ResearcherView";
import { RetrievalCandidates } from "./components/RetrievalCandidates";
import { TrialCard } from "./components/TrialCard";
import { casePresentation, ko, pipelineEventLabels } from "./lib/locale";
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

function finalStages(session: SessionView): Record<string, StageState> {
  if (session.support_level !== "retrieval_only") {
    return Object.fromEntries(stageNames.map((stage) => [stage, "completed"]));
  }
  return Object.fromEntries(
    stageNames.map((stage, index) => [stage, index < 2 ? "completed" : "skipped"]),
  );
}

function completedStatus(nextSession: SessionView): string {
  if (nextSession.support_level === "retrieval_only") {
    return "검색 완료 · 이 사례는 임상시험 검색 결과만 제공하며 적격성 판정은 생성하지 않습니다.";
  }
  if (nextSession.trial_evaluation?.decision === "REVIEW_REQUIRED") {
    return nextSession.current_question?.selected
      ? "상위 후보 검토 필요 · 검증된 조건에서 다음 확인 항목을 선택했습니다."
      : "상위 후보 검토 필요 · 자동 판정할 수 없는 조건이 남아 있습니다.";
  }
  if (nextSession.current_question?.selected) {
    return "분석 완료 · 판정에 가장 유용한 다음 확인 항목을 선택했습니다.";
  }
  if ((nextSession.trial_evaluation?.degradation_codes.length ?? 0) > 0) {
    return "상위 후보 검토 필요 · 자동 판정에 사용할 수 없는 조건이 남아 있습니다.";
  }
  if (nextSession.degradation_codes.length > 0) {
    return "분석 완료 · 일부 하위 후보는 원문 검토가 필요합니다.";
  }
  return "분석 완료 · 현재 기록에서 추가로 제안할 확인 항목이 없습니다.";
}

function displayDate(value: string): string {
  return value.replaceAll("-", ".");
}

function localToday(): string {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function AboutPage() {
  return (
    <main className="about-page min-h-screen px-6 py-16">
      <article className="about-card mx-auto max-w-3xl">
        <Link className="text-sm font-bold text-blue-700" to="/">← 데모로 돌아가기</Link>
        <p className="eyebrow mt-12">연구 프로토타입 소개</p>
        <h1 className="mt-4 text-4xl font-black">TRIAL-OPT</h1>
        <p className="mt-6 text-lg leading-8 text-slate-300">불완전한 환자 설명을 임의의 진단으로 채우지 않고, 현재 기록에서 확인 가능한 근거와 부족한 근거를 구분합니다. 그중 사전 선별 결과에 가장 큰 영향을 줄 수 있는 기존 기록 하나를 다음 확인 대상으로 제안하는 연구 프로토타입입니다.</p>
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
  const [statusText, setStatusText] = useState("S004 스냅샷 데모를 시작할 수 있습니다.");
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
        if (restored.seed_case_id) setSelectedCase(restored.seed_case_id);
        setDegradationCodes(restored.degradation_codes);
        setStages(finalStages(restored));
        setMode(restored.mode === "live" ? "live" : "snapshot");
        setEvaluationDate(restored.evaluation_date);
        setStatusText(completedStatus(restored));
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
    setStatusText("7단계 근거 분석을 실행하고 있습니다…");
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
          setStatusText(pipelineEventLabels[event] ?? "분석 단계를 진행하고 있습니다.");
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
      setStages(finalStages(nextSession));
      setStatusText(completedStatus(nextSession));
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
    setStatusText("검증된 S004 스냅샷 데모를 새 세션으로 시작할 수 있습니다.");
    void navigate("/");
  }

  async function answer(input: {
    answerText?: string;
    structuredValue?: Record<string, unknown>;
    unknown?: boolean;
    declined?: boolean;
  }) {
    if (!credentials || !session?.current_question?.selected) return;
    setBusy(true);
    setError(null);
    setReplayStatus(null);
    setStatusText("답변을 반영해 관련 조건의 근거만 다시 평가하고 있습니다…");
    try {
      await submitAnswer(
        credentials,
        session.current_question.selected.question_id,
        input,
        ({ event }) => {
          updateStage(event);
          setStatusText(pipelineEventLabels[event] ?? "답변을 반영해 판정을 다시 평가하고 있습니다.");
        },
      );
      const updatedSession = await readSession(credentials);
      setSession(updatedSession);
      setDegradationCodes(updatedSession.degradation_codes);
      setStatusText(
        input.unknown || input.declined
          ? "확인할 수 없는 기록으로 표시했습니다. 같은 질문은 다시 제안하지 않습니다."
          : "답변을 반영해 조건별 근거 평가를 완료했습니다.",
      );
    } catch (caught) {
      if (
        caught instanceof Error &&
        caught.name === "SNAPSHOT_BRANCH_UNAVAILABLE" &&
        session.mode === "snapshot"
      ) {
        try {
          const recoveredSession = await readSession(credentials);
          setSession(recoveredSession);
          setDegradationCodes(recoveredSession.degradation_codes);
          setStatusText(caught.message);
          return;
        } catch {
          // Preserve the original actionable error if session recovery also fails.
        }
      }
      setError(caught instanceof Error ? caught.message : "재평가를 완료하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function replay() {
    if (!credentials) return;
    const started = performance.now();
    setReplayStatus("저장된 입력으로 판정 근거를 다시 검증하고 있습니다…");
    const nctId = session?.top_trial?.nct_id ?? session?.trial_evaluation?.nct_id;
    if (!nctId) {
      setReplayStatus("다시 검증할 상위 임상시험 판정 근거가 없습니다.");
      return;
    }
    const result = await replayProof(credentials, nctId);
    const elapsed = Math.round(performance.now() - started);
    setReplayStatus(
      result.passed
        ? `판정 근거 서버 재실행 통과 · Proof Replay ${result.replayCount}/${result.packetCount} · 상태 v${result.patientStateVersion} · ${elapsed} ms`
        : "판정 근거 재검증에 실패했습니다.",
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
      const resetSessionView = await readSession(nextCredentials);
      setSession(resetSessionView);
      setStages(finalStages(resetSessionView));
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

  function selectWorkspaceTab(nextTab: "patient" | "research" | "experiment") {
    setTab(nextTab);
    window.requestAnimationFrame(() => window.scrollTo({ top: 0, behavior: "auto" }));
  }

  function selectMode(nextMode: "snapshot" | "live") {
    setMode(nextMode);
    setEvaluationDate(
      nextMode === "live"
        ? localToday()
        : (configQuery.data?.snapshot_data_date ?? "2026-08-11"),
    );
  }

  if (location.pathname === "/about") return <AboutPage />;

  const hasDegradation = degradationCodes.length > 0;
  const topDegradationCodes = session?.trial_evaluation?.degradation_codes ?? [];
  const hasTopDegradation = topDegradationCodes.length > 0;
  const completionLabel = hasTopDegradation
    ? "상위 후보 검토 필요"
    : hasDegradation
      ? "일부 후보 검토 필요"
      : "분석 완료";

  return (
    <main className="app-shell min-h-screen" aria-busy={busy}>
      <header className={`app-header sticky top-0 z-20 ${session ? "workspace-command-bar" : "landing-product-bar"}`}>
        <div className="app-header-inner mx-auto flex max-w-[1500px] flex-wrap items-center justify-between gap-4">
          <Link to="/" className="brand-lockup group">
            <p className="brand-name">{ko.product.name}</p>
            <p className="brand-descriptor">{ko.product.descriptor}</p>
          </Link>
          <div className="header-meta flex flex-wrap items-center gap-2">
            {session ? <span className="workspace-context">{inputMode === "seed" ? `${selectedCase} 데모` : "직접 입력 사례"} · {completionLabel}</span> : null}
            <span className="mode-badge">{!session ? (mode === "snapshot" ? ko.mode.snapshot : ko.mode.live) : session.mode === "snapshot" ? ko.mode.snapshotShort : ko.mode.liveShort}</span>
            <span className="mode-badge">기준일 {displayDate(session?.evaluation_date ?? evaluationDate)}</span>
            <span className="mode-badge hidden sm:inline-flex">{(session?.mode ?? mode) === "live" ? "라이브 분석 · 비용 guard 적용" : "저장된 분석 · 비용 $0.000"}</span>
            {degradationCodes.length ? <span className="degraded-badge">일부 기능 제한 {degradationCodes.length}건</span> : null}
            <Link className="secondary-button px-3 py-2" to="/about">데모 안내</Link>
          </div>
        </div>
      </header>

      {!session ? (
        <section className="landing-shell mx-auto max-w-[1320px] px-6 py-10">
          <div className="landing-grid grid gap-8 xl:grid-cols-[0.96fr_1.04fr]">
            <div className="landing-hero flex flex-col justify-center">
              <p className="eyebrow">{ko.landing.eyebrow}</p>
              <h1 aria-label="근거가 부족한 지점을 찾고, 다음 확인 질문을 제안합니다." className="mt-4 text-5xl font-black leading-[1.04] tracking-tight sm:text-6xl">{ko.landing.title.split("\n").map((line) => <span key={line}>{line}<br /></span>)}</h1>
              <p className="mt-5 max-w-2xl text-base leading-7 text-slate-300">{ko.landing.description}</p>
              <dl className="landing-stats" aria-label="스냅샷 데모 구성">
                <div><dt>분석 단계</dt><dd>7</dd></div>
                <div><dt>검색 후보</dt><dd>20</dd></div>
                <div><dt>평가 조건</dt><dd>7</dd></div>
              </dl>
              <section className="landing-flow-preview" aria-label="근거 기반 판정 흐름">
                <div className="landing-flow-heading"><strong>답변 하나가 조건별 판정 근거를 어떻게 바꾸는지 확인하세요.</strong><span>근거 추적 흐름</span></div>
                <ol>
                  <li><small>01</small><strong>임상시험 선정 조건</strong></li>
                  <li><small>02</small><strong>환자 기록과 대조</strong></li>
                  <li><small>03</small><strong>부족한 기록 확인</strong></li>
                  <li><small>04</small><strong>판정 근거 재평가</strong></li>
                </ol>
              </section>
              <div className="landing-disclaimer"><Disclaimer /></div>
            </div>

            <section className="panel pre-screen-card" aria-labelledby="input-title">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div><p className="eyebrow">STEP 1 OF 2 · 데모 입력</p><h2 id="input-title" className="panel-title">어떤 환자 정보로 시작할까요?</h2><p className="section-description">준비된 사례를 선택하거나 환자 정보를 직접 입력하세요.</p></div>
                <div className="segmented-control flex rounded-xl p-1">
                  {(["snapshot", "live"] as const).map((item) => <button key={item} onClick={() => selectMode(item)} className={`segmented ${mode === item ? "segmented-active" : ""}`}>{item === "snapshot" ? ko.mode.snapshot : ko.mode.live}</button>)}
                </div>
              </div>
              {mode === "live" ? <p className="runtime-banner runtime-warning mt-3">{configQuery.isPending ? "라이브 모드 사용 가능 여부를 확인하고 있습니다…" : configQuery.data?.live_available ? "라이브 모드 활성화 · first-party Google Cloud ADC와 비용 guard를 사용합니다." : "라이브 모드는 Google Cloud ADC·결제·quota 외부 검증 전까지 비활성입니다. 스냅샷 데모는 계속 사용할 수 있습니다."}</p> : null}
              <div className="input-tabs mt-5 flex gap-2 pb-3">
                <button className={`tab-button ${inputMode === "seed" ? "tab-active" : ""}`} onClick={() => setInputMode("seed")}>준비된 데모 사례</button>
                <button className={`tab-button ${inputMode === "text" ? "tab-active" : ""}`} onClick={() => setInputMode("text")}>환자 설명 직접 입력</button>
              </div>
              {inputMode === "seed" ? (
                <div className="mt-4 grid max-h-64 gap-2 overflow-y-auto pr-1 sm:grid-cols-2">
                  {(casesQuery.data ?? []).map((item) => (
                    <button key={item.id} onClick={() => setSelectedCase(item.id)} className={`seed-card ${selectedCase === item.id ? "seed-card-active" : ""}`}>
                      <span className="flex items-center justify-between"><strong>{item.id}</strong><span className={item.has_full_snapshot ? "text-emerald-300" : "text-slate-500"}>{casePresentation[item.id]?.availability ?? (item.has_full_snapshot ? "전체 데모 제공" : "검색 경로만 제공")}</span></span>
                      <span className="mt-2 line-clamp-2 text-left text-xs leading-5 text-slate-400">{casePresentation[item.id]?.summary ?? item.text}</span>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="mt-4">
                  <label className="text-sm font-bold" htmlFor="patient-text">공개 또는 합성 환자 설명</label>
                  <textarea id="patient-text" className="clinical-input mt-2 min-h-32 w-full p-3 text-sm" maxLength={12000} value={patientText} onChange={(event) => { setPatientText(event.target.value); setIdentifierAcknowledged(false); }} />
                  <label className="mt-3 flex items-start gap-3 text-sm leading-6 text-slate-300"><input type="checkbox" className="mt-1" checked={confirmedSynthetic} onChange={(event) => setConfirmedSynthetic(event.target.checked)} />이 입력은 공개 또는 합성 데이터이며 실제 환자 정보가 포함되지 않았습니다.</label>
                  {identifierRanges.length ? <div role="alertdialog" aria-label="잠재적 식별자 경고" className="mt-3 rounded-xl border border-rose-300/40 bg-rose-300/10 p-4"><p className="font-bold text-rose-200">잠재적 식별자 패턴을 확인하세요</p><ul className="mt-2 text-xs text-slate-300">{identifierRanges.map((item, index) => <li key={`${item.category}-${index}`}>{item.category} · 위치 {item.start}–{item.end}</li>)}</ul><label className="mt-3 flex gap-2 text-xs"><input type="checkbox" checked={identifierAcknowledged} onChange={(event) => setIdentifierAcknowledged(event.target.checked)} />합성 예시임을 다시 확인합니다.</label></div> : null}
                </div>
              )}
              <div className="mt-5 grid gap-3 sm:grid-cols-[1fr_auto]">
                <label className="text-xs font-bold text-slate-400">평가 기준일<input type="date" value={evaluationDate} max={mode === "live" ? localToday() : undefined} disabled={mode === "snapshot"} onChange={(event) => setEvaluationDate(event.target.value)} className="clinical-input mt-1 block w-full px-3 py-2 text-sm" /></label>
                <button aria-label={busy ? "분석 중…" : mode === "live" ? "라이브 분석 시작" : selectedCaseRecord?.has_full_snapshot ? `${selectedCase} 데모 분석 시작` : "전체 데모가 준비되지 않은 사례"} className="primary-button self-end" disabled={!canStart} onClick={() => void start()}>{busy ? "근거 분석 중…" : mode === "live" ? "라이브 근거 분석 시작" : selectedCaseRecord?.has_full_snapshot ? `${selectedCase} 근거 분석 시작` : "전체 데모가 준비되지 않은 사례"}</button>
              </div>
              <p aria-live="polite" className="mt-3 text-center text-xs text-slate-500">{statusText}</p>
              {liveStalled || (mode === "live" && busy && degradationCodes.length) ? <div role="status" className="runtime-banner runtime-warning mt-3"><p>{liveStalled ? "라이브 모드의 외부 분석이 지연되고 있습니다." : "라이브 모드의 일부 외부 기능을 사용할 수 없습니다."} 직접 입력한 내용은 스냅샷 사례에 자동으로 대입하지 않습니다.</p><button className="secondary-button mt-2 py-2" onClick={prepareSnapshotFallback}>현재 요청 중단 후 S004 스냅샷 데모 준비</button></div> : null}
              {error ? <p role="alert" className="mt-3 text-sm text-rose-300">{error}</p> : null}
            </section>
          </div>
        </section>
      ) : (
        <div className="session-layout">
          <aside className="clinical-sidebar" aria-label="Clinical intelligence navigation">
            <div className="sidebar-brand"><span aria-hidden="true">+</span><strong>TRIAL-OPT</strong></div>
            <div className="sidebar-case"><small>{inputMode === "seed" ? "데모 사례" : "직접 입력 사례"}</small><strong>{inputMode === "seed" ? selectedCase : "직접 입력"} · {session.mode === "snapshot" ? "스냅샷" : "라이브"}</strong><span>{session.support_level === "retrieval_only" ? "임상시험 검색 전용" : session.trial_evaluation?.decision === "REVIEW_REQUIRED" || hasDegradation ? "전문가 검토가 필요한 제한적 평가" : "조건별 근거 평가 완료"}</span></div>
            <nav>
              <Link to="/" className="sidebar-nav-item"><span aria-hidden="true">⌂</span><span><strong>사전 선별</strong><small>새 사례 선택</small></span></Link>
              <button className={`sidebar-nav-item ${tab === "patient" ? "sidebar-nav-active" : ""}`} onClick={() => selectWorkspaceTab("patient")}><span aria-hidden="true">◇</span><span><strong>Trial Workspace</strong><small>조건별 판정과 다음 질문</small></span></button>
              <button className={`sidebar-nav-item ${tab === "research" ? "sidebar-nav-active" : ""}`} onClick={() => selectWorkspaceTab("research")}><span aria-hidden="true">◎</span><span><strong>연구 근거</strong><small>질문 선택과 검색 근거</small></span></button>
              <button className={`sidebar-nav-item ${tab === "experiment" ? "sidebar-nav-active" : ""}`} onClick={() => selectWorkspaceTab("experiment")}><span aria-hidden="true">▥</span><span><strong>실험 근거</strong><small>평가 지표와 비교 결과</small></span></button>
            </nav>
            <p className="sidebar-disclaimer">연구용 프로토타입<br />의료 조언이나 최종 적격 판정이 아닙니다.</p>
          </aside>
          <div className="workspace-shell mx-auto max-w-[1500px] px-5 py-4">
          {degradationCodes.length ? <div role="status" className="runtime-banner runtime-warning mb-4"><p><strong>{hasTopDegradation ? "상위 후보는 전문가 검토가 필요합니다." : "일부 하위 후보는 원문 검토가 필요합니다."}</strong> 확인된 조건의 결과는 보존했지만 검증되지 않은 조건은 자동 판정과 우선순위에 유리하게 사용하지 않습니다. <span className="status-code">{degradationCodes.join(" · ")}</span></p><button className="secondary-button mt-2 py-2" onClick={prepareSnapshotFallback}>S004 스냅샷 데모로 새로 시작</button></div> : null}
          <div className="workspace-toolbar mb-3 flex flex-wrap items-center justify-between gap-3 px-4 py-2">
            <p className="text-sm text-slate-300">{statusText}</p>
            <div className="flex flex-wrap gap-2"><button aria-label="Replay Proof" className="secondary-button py-2" disabled={!session.durable_replay} title={session.durable_replay ? undefined : "검색 전용 사례에는 다시 검증할 판정 근거가 없습니다."} onClick={() => void replay()}>{ko.action.replay}</button><button aria-label="Export report" className="secondary-button py-2" disabled={!session.export_available} title={session.export_available ? undefined : session.support_level === "retrieval_only" ? "검색 전용 사례는 적격성 판정 보고서를 생성하지 않습니다." : "일부 저장 기능을 사용할 수 없어 보고서를 저장할 수 없습니다."} onClick={() => credentials && void exportReport(credentials)}>{ko.action.export}</button><button aria-label="Reset session" className="secondary-button py-2" disabled={busy} onClick={() => void resetCurrentSession()}>{ko.action.reset}</button><button aria-label="Delete session" className="danger-button py-2" disabled={busy} onClick={() => void deleteCurrentSession()}>{ko.action.delete}</button></div>
          </div>
          {error ? <p role="alert" className="mb-4 rounded-xl border border-rose-300/40 bg-rose-300/10 p-3 text-sm text-rose-200">{error}</p> : null}
          {replayStatus ? <p aria-live="polite" className="mb-4 rounded-xl bg-emerald-300/10 p-3 text-sm text-emerald-200">{replayStatus}</p> : null}
          {showDemoTools ? <section className="mb-4 rounded-xl border border-dashed border-fuchsia-300/40 bg-fuchsia-300/5 p-3" aria-label="Failure simulation controls"><p className="text-xs font-bold text-fuchsia-200">발표 리허설 전용 · 장애 상태 재현</p><div className="mt-2 flex flex-wrap gap-2">{["GEMINI_UNAVAILABLE", "CTGOV_UNAVAILABLE", "EMBEDDING_UNAVAILABLE"].map((code) => <button key={code} className="secondary-button px-3 py-2 text-xs" onClick={() => toggleFailure(code)}>{degradationCodes.includes(code) ? "✓ " : ""}{code}</button>)}</div></section> : null}

          <div className={`workspace-grid workspace-primary ${tab === "patient" ? "" : "workspace-primary-evidence"}`}>
            <div className="flex min-h-0 flex-col gap-3"><AgentTimeline states={stages} /><QuestionPanel session={session} busy={busy} onAnswer={(branch) => void answer(branch)} /></div>
            <div className="min-h-0 space-y-3 overflow-y-auto"><TrialCard session={session} /><section className="panel patient-source-card p-4"><p className="eyebrow">환자 설명 원문</p><p className="patient-source-summary">{casePresentation[selectedCase]?.summary ?? "입력된 환자 설명"}</p><details className="source-original"><summary>영어 원문 보기</summary><div className="source-original-content"><p>{session.patient_text}</p></div></details></section></div>
            <div className="flex min-h-0 flex-col gap-3"><EvidenceFirewall session={session} /><CriterionMatrix session={session} /></div>
          </div>

          <nav className="workspace-tabs mt-3 flex gap-1 p-1" aria-label="Workspace evidence tabs">{(["patient", "research", "experiment"] as const).map((item) => <button aria-label={item === "patient" ? "Patient Summary" : item === "research" ? "Researcher View" : "Experiment Evidence"} key={item} onClick={() => selectWorkspaceTab(item)} className={`workspace-tab ${tab === item ? "workspace-tab-active" : ""}`}>{item === "patient" ? "환자·판정" : item === "research" ? "연구 근거" : "실험 근거"}</button>)}</nav>
          <div className="evidence-view mt-4"><Suspense fallback={<section className="panel runtime-loading">근거 화면을 불러오고 있습니다…</section>}>{tab === "patient" ? <div className="grid gap-4 xl:grid-cols-[1fr_0.8fr]"><ProofGraph session={session} />{retrieval ? <RetrievalCandidates retrieval={retrieval} /> : <EmptyState>검색 후보가 세션에 기록되지 않았습니다. 현재 판정과 완료된 근거 평가는 그대로 유지됩니다.</EmptyState>}</div> : tab === "research" ? <div className="research-evidence-layout"><ResearcherView session={session} /><ProofGraph session={session} />{retrieval ? <RetrievalCandidates retrieval={retrieval} /> : <EmptyState>검색 후보가 세션에 기록되지 않았습니다. 현재 판정과 완료된 근거 평가는 그대로 유지됩니다.</EmptyState>}</div> : <ExperimentEvidence />}</Suspense></div>
          <div className="workspace-disclaimer mt-5"><Disclaimer /></div>
          </div>
        </div>
      )}
    </main>
  );
}

import { Disclaimer } from "./components/Disclaimer";

export function App() {
  return (
    <main className="min-h-screen bg-slate-950 px-6 py-12 text-slate-100">
      <section className="mx-auto max-w-5xl rounded-3xl border border-cyan-300/20 bg-slate-900 p-10 shadow-2xl">
        <p className="mb-3 text-sm font-semibold tracking-[0.22em] text-cyan-300">TRIAL-OPT</p>
        <h1 className="max-w-3xl text-4xl font-bold leading-tight">
          근거 증명형 능동 정보 획득 기반 임상시험 사전 선별
        </h1>
        <p className="mt-5 max-w-3xl text-lg leading-8 text-slate-300">
          검색 가설과 판정 근거를 분리하고, 재생 가능한 기준별 증명과 다음 한 가지 확인 질문을
          제공합니다.
        </p>
        <div className="mt-10">
          <Disclaimer />
        </div>
      </section>
    </main>
  );
}


# TRIAL-OPT

TRIAL-OPT is a Korean-first research prototype for proof-carrying active evidence acquisition in interactive clinical-trial pre-screening. It separates retrieval hypotheses from eligibility evidence, produces replayable criterion decisions, and asks one decision-relevant follow-up action at a time.

> This system is a research prototype for clinical-trial pre-screening using public and synthetic data. It does not diagnose disease, provide medical advice, determine final eligibility, or replace review by a qualified clinical-trial team.

The repository is being implemented phase-by-phase from `TRIAL_OPT_FINAL_DEVELOPMENT_SPEC.md`. The offline quick start, full architecture, evaluation commands, deployment guide, and release artifact identifiers will be completed in their specification-defined phases.

## Offline Phase-2 demo

```bash
make bootstrap
make lint
make test
cd frontend && npm run e2e && cd ..
make demo-offline
```

Open `http://localhost:8080`, select the frozen S004 Snapshot flow, and use pinned branch A or B.
The page also shows the bounded Phase-2 retrieval pool (20 retained, top 8 selected and visibly
"not compiled"). The health endpoint is available at `http://localhost:8080/api/v1/health`.

The primary demo is offline: it makes no ClinicalTrials.gov or Gemini request. The tracked
Phase-2 fixture is hash-bound to the official API v2 source timestamp. Live CTGov retrieval,
exact-byte local caching, three-source RRF, lexical fallback, and automatic snapshot fallback are
implemented and covered independently. The deterministic recorded embedding vectors are test
fixtures, not claims of a live Gemini run.

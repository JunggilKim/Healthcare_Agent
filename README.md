# TRIAL-OPT

TRIAL-OPT is a Korean-first research prototype for proof-carrying active evidence acquisition in interactive clinical-trial pre-screening. It separates retrieval hypotheses from eligibility evidence, produces replayable criterion decisions, and asks one decision-relevant follow-up action at a time.

> This system is a research prototype for clinical-trial pre-screening using public and synthetic data. It does not diagnose disease, provide medical advice, determine final eligibility, or replace review by a qualified clinical-trial team.

The repository is being implemented phase-by-phase from `TRIAL_OPT_FINAL_DEVELOPMENT_SPEC.md`. The offline quick start, full architecture, evaluation commands, deployment guide, and release artifact identifiers will be completed in their specification-defined phases.

## Phase 0 development

```bash
make bootstrap
make lint
make test
make demo-offline
```

The health endpoint is available at `http://localhost:8080/api/v1/health`.

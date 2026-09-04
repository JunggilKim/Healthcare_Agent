# TRIAL-OPT

TRIAL-OPT is a Korean-first research prototype for **proof-carrying active evidence acquisition**
in interactive clinical-trial pre-screening. Instead of guessing from an incomplete description,
it separates retrieval hypotheses from admissible eligibility evidence, produces replayable
criterion verdicts, and asks for one existing record or value with the highest fixed utility.

> 🏆 **Healthcare Agentic AI Challenge 2026 우수상 (3위)**
> Competition result and archival artifacts are recorded in
> [the final project archive](docs/operations/FINAL_PROJECT_ARCHIVE_20260905.md).

## Research contribution

- **Active evidence acquisition:** a bounded B6 policy simulates answer branches and selects one
  slot by risk reduction, decision resolution, discrimination, coverage, burden, and sensitivity.
- **Proof-carrying verdicts:** every hard verdict is tied to an exact protocol hash, patient fact
  IDs, deterministic AST derivation, and PV-001–PV-015 verifier results.
- **Evidence firewall:** symptom/imaging-derived disease concepts may retrieve trials as Grade-H
  hypotheses but cannot satisfy or fail eligibility criteria.

> This system uses public and synthetic data for research pre-screening. It does not diagnose
> disease, provide medical advice, determine final eligibility, or replace review by a qualified
> clinical-trial team. Never enter real medical records or personal identifiers.

## Architecture

```mermaid
flowchart LR
  A["Synthetic/public input"] --> B["Patient Evidence Agent"]
  B --> C["Retrieval Query Agent"]
  C --> D["CTGov + BM25 + embedding RRF"]
  D --> E["Protocol Compiler + semantic review"]
  E --> F["Deterministic AST evaluator"]
  B --> F
  F --> G["PV-001–PV-015 proof verifier"]
  G --> H["Deterministic ranker"]
  H --> I["TRIAL-OPT branch simulator"]
  I --> J["One existing-record/value request"]
  J --> F
  G --> K["Proof replay and report export"]
```

FastAPI owns the API, orchestration, deterministic engine, SQLite/local artifact adapters, and
optional Firestore/GCS adapters. React 19 is compiled to static files served by FastAPI. Snapshot
Mode needs no external service; Live Mode uses first-party Google Cloud ADC and the official
ClinicalTrials.gov API v2.

## Snapshot Demo quick start

The independently verified offline path is S004/NCT05239624. The complete S004/S008/S001 release
snapshot is pending exact-hash model review and project adjudication; see
`docs/operations/PENDING_EXTERNAL_VALIDATION.md`.

```bash
git clone <repository-url>
cd trial-opt
docker build -t trial-opt:local .
docker run --rm -p 8080:8080 \
  -e APP_ENV=local \
  -e STORE_BACKEND=local \
  -e DEFAULT_RUNTIME_MODE=snapshot \
  trial-opt:local
```

Open `http://localhost:8080`, keep `Snapshot Demo` selected, choose S004, and use a pinned answer.
No Google credentials or outbound network are required for this verified vertical slice.

Native development:

```bash
make bootstrap
make test-offline
make demo-offline
```

## Optional Live Mode setup

Live Mode is not part of the primary demo. The release deployment completed one explicit Live
Mode smoke on Google Cloud; its observed cold analysis latency is recorded in the release evidence
and is not substituted for the larger performance sample. This project uses Google Cloud
first-party endpoints with ADC, not Google AI Studio billing or API keys.

```bash
gcloud auth application-default login
gcloud config set project <PROJECT_ID>
export GOOGLE_CLOUD_PROJECT=<PROJECT_ID>
export GOOGLE_CLOUD_LOCATION=global
export STORE_BACKEND=local
export ALLOW_LIVE_MODEL_CALLS=true
export ALLOW_LIVE_CTGOV_CALLS=true
make live-local
```

## Environment variables

Copy `.env.example` for local nonsecret settings. Production must use `APP_ENV=prod`,
`STORE_BACKEND=gcp`, an exact frozen `DEMO_SNAPSHOT_VERSION`, and Secret Manager mappings for
`SESSION_TOKEN_HMAC_SALT` and `IP_HASH_SALT`. Never commit actual values.

## Test commands

```bash
make lint
make typecheck
make test-offline
npm run e2e --workspace frontend
make frontend-build
make docker-build
```

The normal test suite makes no paid model call. Playwright blocks outbound traffic and exercises
the snapshot flow, unknown/declined paths, failure rehearsal, accessibility, direct SPA reload,
and 1440×900 visual baselines.

## Benchmark and evaluation

```bash
uv run python scripts/generate_benchmark.py --config config/eval.yaml --seed 20260811
uv run python scripts/evaluate.py --suite retrieval --config config/eval.yaml
uv run python scripts/evaluate.py --suite criterion --config config/eval.yaml
uv run python scripts/evaluate.py --suite interactive --policies all --seed 20260811
uv run python scripts/evaluate.py --suite ablation --all
uv run python scripts/acquire_trec.py
uv run python scripts/render_eval_report.py --latest
```

The reviewed Dataset A handoff uses `scripts/prepare_annotations.py` and
`scripts/validate_annotations.py`. They produce blinded, exact-hash JSONL assignments and reject
incomplete or non-independent adjudication. See `docs/guides/ANNOTATION_GUIDE.md` for the versioned
world-generation, two-pass paraphrase validation, and review-row commands.

Release evaluation additionally uses these non-imputable evidence stages:

- `scripts/prepare_retrieval_evidence.py` creates blinded held-out qrel assignments and freezes
  reviewer labels together with recorded system runs produced by
  `scripts/materialize_retrieval_runs.py`;
- `scripts/prepare_b5_policy.py` runs the sequential B5 direct-question batch against the exact
  candidate list at each oracle step;
- `scripts/prepare_proof_baselines.py` prepares and freezes paid P0/P1 batch predictions;
- `scripts/measure_snapshot_performance.py` measures at least 20 local HTTP runs, while
  `scripts/validate_performance_evidence.py` validates the combined commit-bound Snapshot/Live
  evidence. The renderer refuses release status without every suite and performance artifact.

Committed Phase-6 numbers are explicitly `acceptance_eligible=false`: they are a one-trial S004
structured engineering smoke, not Dataset A, stable-top-3 evidence, or clinical validation. The
24-trial three-case corpus and 310-world Dataset A are prepared. Exact-hash snapshot review,
complete-world annotation of at least 200 pairs, retrieval relevance review, and the review-bound
B5/P0/P1 baselines remain pending external reviewer input. No missing result is imputed.

## GCP deployment summary

After project, billing, and role approval, run `scripts/bootstrap_gcp.sh`, then `make deploy`. The
frozen production command uses Cloud Run in `asia-northeast3`, one Uvicorn worker, concurrency 4,
two named Secret Manager mappings, and no service-account key. Run
`scripts/smoke_test_deployment.sh --base-url URL`; `--live` is explicit and makes exactly one live
session. Full instructions are in the scripts and
`docs/spec/TRIAL_OPT_FINAL_DEVELOPMENT_SPEC.md`.

The public competition demo ran on Cloud Run in `asia-northeast3`. Its final revision served
commit `8a9c300` at the recorded immutable image digest, and the deployment passed the bounded
Snapshot and Live smoke checks documented in this repository. The service and project-specific
runtime resources were decommissioned after the competition on 2026-09-05 to stop ongoing costs.
The source, submitted package, presentation, hashes, and final cloud identifiers remain available
through the repository and its archival GitHub release; see
[the final project archive](docs/operations/FINAL_PROJECT_ARCHIVE_20260905.md).

## Data sources and terms

See [DATA_SOURCES.md](docs/reference/DATA_SOURCES.md) for organizer seeds, ClinicalTrials.gov attribution and
terms, the exact TREC adapter status, and project-generated synthetic benchmark limitations. See
[THIRD_PARTY_NOTICES.md](docs/reference/THIRD_PARTY_NOTICES.md) for dependency licensing.

## Models and cost assumptions

Frozen routing uses `gemini-3.6-flash`, `gemini-3.5-flash-lite`, and
`gemini-embedding-001`; deterministic code controls verdicts, proof replay, ranking, and policy.
The normal cold-session target is $0.45 and the hard reservation cap is $1.25. These are engineering
budgets, not billing guarantees. Details are in
[MODEL_AND_COST_CARD.md](docs/reference/MODEL_AND_COST_CARD.md).

## Known limitations

The exact-hash snapshot review and Dataset A/retrieval reviewer labels remain pending external
validation. Those labels are prerequisites for final P0/P1/B5 evidence and acceptance-eligible
metrics. The one observed production cold Live analysis took 206.17 seconds, above the 90-second
release target; it is reported as measured and is not imputed away. The bounded AST cannot
formalize all protocol language; material unsupported text stays `OPAQUE`. ClinicalTrials.gov
records, APIs, models, and prices can become stale. False positives and false negatives remain
possible. See [SAFETY_AND_LIMITATIONS.md](docs/reference/SAFETY_AND_LIMITATIONS.md).

## References

- ClinicalTrials.gov API v2 and [Terms and Conditions](https://clinicaltrials.gov/about-site/terms-conditions)
- [TREC 2022 Clinical Trials Track](https://trec.nist.gov/data/trials2022.html)
- [ir_datasets Clinical Trials adapter](https://ir-datasets.com/clinicaltrials.html)
- Full contract: [`TRIAL_OPT_FINAL_DEVELOPMENT_SPEC.md`](docs/spec/TRIAL_OPT_FINAL_DEVELOPMENT_SPEC.md)

## Release artifact identifiers

- Source commit: run `git rev-parse HEAD`; final tag remains gated on strict acceptance.
- Snapshot hash: `data/demo/current/manifest.json` (three cases; exact-hash review pending).
- Historical Docker image digest and production URL: recorded in the final project archive; the
  endpoint is intentionally offline after decommissioning.
- Evaluation run IDs: recorded in `artifacts/eval/latest/metrics.json` (fixture smoke only).
- Tag `v1.0.0-challenge`: pending strict acceptance; not fabricated here.

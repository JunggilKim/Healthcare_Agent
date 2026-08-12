# Pending External Validation

This file records checks that require user-controlled credentials, billing, quota, capacity, or
local infrastructure state. They are not represented as completed.

## Google Cloud and Gemini

- Status: first-party model access and one bounded production Live smoke completed on
  `project-5ff8dae0-85cf-4767-acd`; final human corpus review remains pending.
- Confirmed: Application Default Credentials, billing, `gemini-3.6-flash`,
  `gemini-3.5-flash-lite`, and 768-dimensional `gemini-embedding-001` access all passed using the
  global v1 first-party endpoint and Standard PayGo. The application cost ledger recorded less
  than $5 of reconciled model usage at the final controlled run checkpoint.
- Completed independently: first-party `google-genai==2.17.0` enterprise client construction,
  API v1 selection, fixed model routing, structured schemas, retry/circuit/fallback behavior,
  local and Firestore shared model caches, usage metadata parsing, transactional session/day/total
  cost reservations, Firestore fixed-window rate limits, price estimation, trusted proposal
  validation, GCS offload for session state above 750 KiB, opaque safety fallback, live corpus
  acquisition/compilation, and production orchestration.
- Performance boundary: the final controlled steady-state Live measurement passed the warm target
  (20 runs, p95 below 1 second), but the one commit-bound Cloud Run cold Live smoke took 159.67
  seconds and therefore missed the 90-second cold target. The request still completed with HTTP
  200 and retained visible conservative degradation codes. This release must not claim the cold
  Live latency gate passed.
- Snapshot consequence: a complete three-case, 24-trial, 77-file hash-verified snapshot is
  committed. Its exact-hash manual-review manifest deliberately remains
  `PENDING_EXTERNAL_REVIEW`; no reviewer identity, approval, or assessment was imputed.

## Docker Desktop

- Status: completed for the current release path.
- Confirmed: the image builds successfully, the full Snapshot flow passes in a container with
  outbound networking disabled, and an immutable Artifact Registry digest is recorded after each
  deployment. No user Docker data was deleted by the release workflow.

## Dataset A, manual adjudication, and paid baselines

- Status: human input pending; deterministic fixture evaluation refreshed.
- Blocker: the exact-hash reviewed 24–36-trial Dataset A corpus, at least 200 project-reviewer
  criterion annotations, 50 dual reviews, and adjudication metadata do not yet exist. Paid direct
  LLM baselines B5/P0/P1 also require approved Google Cloud execution.
- Completed independently: a generic verified-AST world generator covering full-pass, isolated and
  multi-failure, unknown, conflict, and boundary worlds; exact narrative fact-span hashes; the
  fixed-seed 30% Korean/English Flash-Lite batch request builder; a second primary-model extraction
  batch that rejects any paraphrase whose typed facts and spans are not fully recovered; blinded
  hash-bound annotation assignment; independent dual-review/adjudication validation; strict
  recomputation of the final JSONL handoff; deterministic S004 engineering-smoke worlds (9 worlds,
  54 missingness observations, 63 criterion labels); B0/B1/B2/B3/B4/B6 policy code; P2/P3 proof
  baselines; config-driven A1–A8 ablations with eval-only safety guards; retrieval metric code;
  charts; static UI JSON; and fixed-seed reproduction commands. Paid batch submission is guarded by
  both a CLI flag and `ALLOW_PAID_BATCH_CALLS=true` and was not invoked.
- Claim boundary: all committed Phase 6 numbers are marked `acceptance_eligible=false` and
  `project-created S004 structured fixture engineering smoke`. They are not Dataset A results,
  clinical validation, stable-top-3 evidence, or a basis for claiming the release thresholds.

## TREC 2022 Clinical Trials Track

- Status: non-blocking external-validity run pending.
- Blocker: `ir_datasets` and the licensed/local frozen 2021 ClinicalTrials.gov corpus were not
  supplied. `artifacts/eval/trec2022/not_run.json` records the exact dataset identifier and
  acquisition instructions; no TREC score is imputed.

## Minimal external completion checklist

Perform these only after approving the named billing/project and Docker cleanup scope:

1. Have project reviewers inspect the exact committed S004/S008/S001 snapshot hashes and populate
   only the pending identity, timestamp, checks, and approval fields in
   `data/demo/manual_review.yaml`.
2. Complete the Dataset A 200-pair/50-dual-review adjudication and supply the paid B5/P0/P1 result
   files; then rerun all release evaluation suites.
3. Resolve or explicitly accept the measured 159.67-second cold Cloud Run Live latency miss before
   claiming Section 101 performance acceptance.
4. Rebuild the reviewed snapshot within the required 48-hour release window, rerun the strict
   verifier, create the release tag only after it passes, and then build the final submission
   package.

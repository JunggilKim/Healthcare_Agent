# Pending External Validation

This file records checks that require user-controlled credentials, billing, quota, capacity, or
local infrastructure state. They are not represented as completed.

## Google Cloud and Gemini

- Status: pending.
- Blocker: no approved Google Cloud project, Application Default Credentials, billing/quota, or
  explicit authorization to make paid model calls was supplied in the current implementation run.
- Completed independently: first-party `google-genai==2.17.0` enterprise client construction,
  API v1 selection, fixed model routing, structured schemas, retry/circuit/fallback behavior,
  local and Firestore shared model caches, usage metadata parsing, transactional session/day/total
  cost reservations, Firestore fixed-window rate limits, price estimation, trusted proposal
  validation, GCS offload for session state above 750 KiB, and opaque safety fallback. The full
  first-party Live orchestrator is wired but has not dispatched a paid request in this run.
- Required external proof later: model-access smoke test for `gemini-3.6-flash`,
  `gemini-3.5-flash-lite`, and `gemini-embedding-001`; paid compilation/review of the final curated
  corpus; quota and cost-guard validation. The current S004 top-8 compiled cache is deliberately
  opaque/review-required because no model output was available. Therefore the specification's
  full-corpus S004/S008/S001 first-question golden assertions cannot yet be represented as passed;
  only the frozen S004 vertical-slice question contract and credential-independent S001/S008
  evidence-firewall/domain-path tests are confirmed.
- Snapshot consequence: `scripts/build_demo_snapshot.py --mode live` intentionally refuses to
  fabricate the missing reviewed artifacts. Phase 5's complete three-case snapshot exit criterion
  and Phase 6's final experiment tab evidence remain pending until those exact-hash artifacts exist.

## Docker Desktop

- Status: pending.
- Blocker: Docker Desktop's internal image storage was full while pulling the base image
  (`no space left on device`). Host storage had free capacity, but Docker reported reclaimable
  images belonging to the user; they were not deleted without approval.
- Completed independently: dependency bootstrap, native lint/typecheck/tests, production frontend
  build, Uvicorn API run, and Chromium E2E.
- Required external proof later: recover Docker storage, build the release image, run it with
  outbound networking disabled in Snapshot Mode, and record its digest.

## Dataset A, manual adjudication, and paid baselines

- Status: pending.
- Blocker: the exact-hash reviewed 24–36-trial Dataset A corpus, at least 200 project-reviewer
  criterion annotations, 50 dual reviews, and adjudication metadata do not yet exist. Paid direct
  LLM baselines B5/P0/P1 also require approved Google Cloud execution.
- Completed independently: deterministic AST-derived S004 engineering-smoke worlds (9 worlds,
  54 missingness observations, 63 criterion labels), B0/B1/B2/B3/B4/B6 policy code, P2/P3 proof
  baselines, config-driven A1–A8 ablations with eval-only safety guards, retrieval metric code,
  charts, static UI JSON, and fixed-seed reproduction commands.
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

1. Recover enough Docker Desktop internal storage, then run the strict verifier's Docker/offline
   container gates and record the immutable image digest.
2. Supply the approved Free Trial GCP project/billing account, run `scripts/bootstrap_gcp.sh`, and
   execute the one-call model-access smoke for all three frozen model IDs.
3. Acquire, manually review, and exact-hash freeze the S004/S008/S001 24–36-trial snapshot within
   48 hours of release.
4. Complete the Dataset A 200-pair/50-dual-review adjudication and rerun all evaluation suites.
5. Deploy the verified commit, run `scripts/smoke_test_deployment.sh --live` exactly once, record
   three rehearsals including the network-disabled container, and rerun
   `uv run python scripts/verify_release.py --strict` from a clean worktree.

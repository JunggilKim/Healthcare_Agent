# Data Sources

TRIAL-OPT separates source data, recorded engineering fixtures, and project-generated evaluation
data. None of these are clinical ground truth unless explicitly stated.

## Organizer synthetic cases

- Source: challenge-provided synthetic patient JSON, preserved in
  `data/seeds/synthetic-patients.json`.
- Use: input seeds and golden demonstration stories S001–S010.
- The source wording is preserved. The project does not claim that the organizer supplied
  trial-matching or eligibility labels.
- These cases must never be replaced with real patient data.

## ClinicalTrials.gov

- Source: official ClinicalTrials.gov API v2.
- API documentation: <https://clinicaltrials.gov/data-api/api>
- Terms: <https://clinicaltrials.gov/about-site/terms-conditions>
- Fields used: NCT ID, overall status, brief/official title, conditions, interventions,
  eligibility criteria, sex, minimum/maximum age, study type, locations, sponsor/collaborators,
  contacts, and record dates when present.
- The checked-in S004 retrieval fixture records `apiVersion=2.0.5` and registry data timestamp
  `2026-08-11T09:00:06` in its manifest. Exact response and record hashes are stored beside it.
- NCT05239624 complete-record SHA-256:
  `6e342382edc485fd43d567e5b5029b7f4bff550354698199af4320bb6d034532`.
- Retrieval and snapshot dates, record IDs, byte sizes, and hashes are authoritative only when
  present in the relevant manifest; do not infer freshness from this document.

ClinicalTrials.gov records are public-source inputs, are not relicensed under this repository's
MIT license, and can change after capture. The U.S. government, National Library of Medicine,
ClinicalTrials.gov, sponsors, and data submitters do not endorse TRIAL-OPT. Users must confirm
current protocol and recruitment information with the trial team.

### Recorded retrieval and compilation fixtures

`data/fixtures/retrieval/S004/embeddings.json` is a deterministic recorded engineering fixture,
not a Gemini-produced research corpus. The Phase-3 top-eight compiled fixtures preserve complete
eligibility text as source-bound `OPAQUE` spans because no authenticated semantic compiler/reviewer
run has been approved. They verify adapters, hashing, coverage, conservative degradation, and proof
blocking; they do not qualify the full snapshot or evaluation acceptance claims.

## TREC 2022 Clinical Trials Track

- Official track: <https://trec.nist.gov/data/trials2022.html>
- Adapter identifier: `clinicaltrials/2021/trec-ct-2022` via `ir_datasets`.
- Acquisition script: `scripts/acquire_trec.py`.
- Status: **not run** in the committed release candidate because the licensed/historical corpus and
  `ir_datasets` environment were unavailable. The script writes a machine-readable `not_run.json`;
  no TREC metric is fabricated.

Any future run must follow the official corpus acquisition conditions and record corpus hashes,
adapter version, query/qrels hashes, and environment metadata.

## Project-generated synthetic benchmark

- Procedure: deterministic AST templates from the pinned S004 structured fixture generate complete
  synthetic worlds; MCAR and realistic missingness masks at 20%, 40%, and 60% produce observations.
- Truth: complete-world structured values and evaluator outcomes are deterministic, not model labels.
- Source binding: the benchmark manifest records source protocol and prompt/config hashes.
- Paraphrasing: no model paraphrasing was used in the committed smoke run. If enabled later, only
  surface wording may change and the exact model/prompt hash must be recorded.
- Split/seed: NCT-ID grouping is required; committed smoke seed is `20260811`. The current one-trial
  fixture cannot provide a meaningful held-out NCT-ID test split.
- Committed scope: 9 worlds, 54 masked observations, and 63 criterion labels for S004 only. Exact
  counts and hashes are in `artifacts/eval/latest/`.

This benchmark is an engineering consistency smoke. It is not Dataset A, a representative disease
sample, clinical validation, evidence of patient benefit, or proof of real-world matching quality.

# Dataset A Annotation Guide

This guide governs the mandatory expert-reviewed evaluation set. It does not authorize use of real
patient data. Annotation uses only public ClinicalTrials.gov protocol text and project-generated
synthetic patient worlds.

## Unit and roles

The unit is one `(synthetic patient world, NCT ID, criterion ID)` record bound to exact source and
fact hashes. Two independent annotators label each record; a third adjudicator resolves conflicts.
Annotators must not see the system verdict or baseline output before submitting their first label.

## Labels

- `PASS`: explicit admissible evidence satisfies the normalized criterion.
- `FAIL`: explicit admissible evidence contradicts the normalized criterion.
- `UNKNOWN`: available evidence cannot decide the criterion, including missing or incompatible data.
- `CONFLICT`: two or more admissible facts disagree and the conflict is unresolved.
- `OPAQUE`: source meaning cannot be represented safely in the bounded AST.
- `NOT_APPLICABLE`: the record is outside the adjudicated pairing definition; never treat as pass.

For each label record source direction, materiality, exact protocol span, required slot/operator,
supporting fact IDs, and a short evidence-linked rationale. Retrieval relevance uses a separate
three-level label: relevant, partially relevant, irrelevant.

## Rules

1. Judge only the pinned source text and explicit structured facts; do not add clinical assumptions.
2. Preserve AND/OR/NOT scope, numeric boundaries, units, and temporal reference points.
3. Normalize an exclusion into a requirement while retaining `source_direction=EXCLUSION`.
4. Missing text is `UNKNOWN`, not evidence of absence.
5. Retrieval-only Grade-H hypotheses can affect relevance but never criterion PASS/FAIL.
6. Use `OPAQUE` for investigator judgment or other material semantics outside the catalog.
7. Do not infer final eligibility, safety, benefit, or enrollment.

## Sampling and split

The reviewed corpus contains 24–36 diverse trials and at least 200 independently annotated
criterion–patient pairs. Split by NCT ID before generating observations so no protocol appears in both
train/development and held-out test. Freeze source hashes, seed, split IDs, annotation-tool version,
and rubric version before adjudication.

## Reproducible review handoff

First generate worlds from the exact reviewed AST corpus. Release mode rejects a corpus outside
24–36 unique interventional trials, unverified protocols, failed boundary tests, unsatisfiable
critical criteria, or fewer than 5 generated worlds for any trial:

```bash
uv run python scripts/generate_benchmark.py --mode release \
  --compiled-trials data/demo/current/sessions/S004/compiled_trials.json \
  --compiled-trials data/demo/current/sessions/S008/compiled_trials.json \
  --compiled-trials data/demo/current/sessions/S001/compiled_trials.json \
  --raw-trials data/demo/current/sessions/S004/raw_trials.json \
  --raw-trials data/demo/current/sessions/S008/raw_trials.json \
  --raw-trials data/demo/current/sessions/S001/raw_trials.json \
  --evaluation-date 2026-08-11 \
  --output data/eval/generated/benchmark.pre-paraphrase.json
```

Then use `scripts/paraphrase_benchmark.py` in three stages: `prepare` creates the exact fixed-seed
Flash-Lite batch JSONL for 30% of worlds (maximum 120, Korean/English balanced),
`prepare-validation` consumes those batch responses and creates a primary-model patient-extraction
batch, and `apply` accepts only narratives for which every original typed fact and exact source span
is recovered. Uploading JSONL and submitting either paid job is an explicit operation; use
`scripts/submit_gemini_batch.py` only with both `--allow-paid-batch` and
`ALLOW_PAID_BATCH_CALLS=true`. Failed paraphrases are not imputed or silently retained as valid.

After the validated release benchmark exists, create a fresh versioned directory;
the command refuses to overwrite prior review material:

```bash
uv run python scripts/prepare_annotations.py \
  --benchmark data/eval/generated/benchmark.json \
  --compiled-trials data/demo/current/sessions/S004/compiled_trials.json \
  --compiled-trials data/demo/current/sessions/S008/compiled_trials.json \
  --compiled-trials data/demo/current/sessions/S001/compiled_trials.json \
  --output-dir data/eval/annotations/round-1
```

`assignments.jsonl` deliberately contains neither generated truth nor system predictions. Reviewers
receive the exact source span, normalized AST, materiality, required slots, and structured facts so
they can judge executability without seeing a system verdict. Reviewers append
`trial-opt-annotation-review-v1` rows to a separate `reviews.jsonl`. Each row binds the
assignment hash, reviewer alias, role (`PRIMARY`, `SECONDARY`, or `ADJUDICATOR`), revision,
timestamp, verdict, evidence fact IDs, missing slot IDs, executability, explanation support, and a
short rationale. Exactly the hash-selected dual-review records receive a secondary review. A
disagreement requires a distinct adjudicator and a disagreement reason.

Validate and freeze the handoff without overwriting previous rounds:

```bash
uv run python scripts/validate_annotations.py \
  --assignments data/eval/annotations/round-1/assignments.jsonl \
  --reviews data/eval/annotations/round-1/reviews.jsonl \
  --output-dir data/eval/annotations/adjudicated-1 \
  --publish-manifest data/eval/annotations/manifest.json
```

The final command refuses fewer than 200 completed records, fewer than 50 completed independent
dual reviews, unresolved disagreements, changed assignment hashes, unknown evidence IDs, or reused
reviewer identities. Publishing is allowed only for a complete review and binds the three JSONL
paths and hashes into the root manifest consumed by the strict verifier. `--allow-incomplete` may
produce an explicitly pending progress manifest; it cannot produce `status=ADJUDICATED`.

The release selector treats 200 as a minimum and keeps every selected patient-world criterion set
complete. This is required to calculate the specified trial-level false pre-screen pass rate; the
metric is never approximated from isolated criterion PASS errors.

## Retrieval and paid baseline handoff

Run `scripts/prepare_retrieval_evidence.py prepare-review` to produce rank-blinded relevance
assignments for every held-out world against the frozen corpus. After review, materialize the five
retrieval baselines from recorded `RetrievalResult` objects with
`scripts/materialize_retrieval_runs.py`, then use `prepare_retrieval_evidence.py finalize` to bind
qrels, system orders, exact-condition flags, the snapshot manifest, and the run commit.

P0/P1 use `scripts/prepare_proof_baselines.py prepare` and `finalize`. B5 is sequential: repeat
`scripts/prepare_b5_policy.py prepare-step`, submit its JSONL through the explicitly gated
`scripts/submit_gemini_batch.py`, and run `apply-step` until all observations stop or reach the
question budget; then run `finalize`. Every recorded B5 choice is replayed against the actual
candidate list and rejected if the model invented a slot.

Local Snapshot timing is collected with:

```bash
uv run python scripts/measure_snapshot_performance.py --runs 20 \
  --output artifacts/eval/performance/snapshot.json
```

Combine it only with actual controlled Live, fallback, log-scan, and Cloud Run startup samples in
the `trial-opt-performance-v1` schema. Validate that final evidence with
`scripts/validate_performance_evidence.py`; do not copy local timings into Live fields.

## Quality control

Record raw independent labels, disagreement reason, final adjudicated label, adjudicator ID alias,
and timestamp. Report agreement before adjudication (percent agreement and Cohen's kappa where
defined), disagreement counts by category, and all exclusions. A changed source hash invalidates the
label until re-reviewed. Never overwrite prior decisions; append a revision with provenance.

## Acceptance handoff

Export schema-valid JSONL plus a manifest containing counts, split NCT IDs, hashes, seed, annotator
aliases, agreement metrics, adjudication completion, and limitations. `verify_release.py --strict`
must reject Dataset A evidence without this manifest and without complete adjudication.

The final `artifacts/eval/latest/metrics.json` is distinct from the committed fixture-smoke report.
It must set `acceptance_eligible=true`, bind `source_git_sha` to an ancestor of the release commit,
and bind `config_hash` and `random_seed` to `config/eval.yaml`. Its `acceptance_metrics` object must
contain every machine value consumed by `scripts/verify_release.py`: matching/retrieval thresholds;
all eight Section 101.2 safety invariants; protocol minimum/mean coverage, boundary-test pass rate,
top-3 material opaque rate, and semantic-review approval rate; B6/B3 question and accuracy values,
the statistical-tie flag, observed maximum question count, hard budget, and repeat-seed identity;
and the measured Snapshot, Live, dependency-fallback, and container startup performance values with
the required run counts. Missing values are failures, not permission to copy fixture-smoke numbers.

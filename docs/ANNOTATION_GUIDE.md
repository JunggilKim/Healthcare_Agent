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
patient–trial pairs. Split by NCT ID before generating observations so no protocol appears in both
train/development and held-out test. Freeze source hashes, seed, split IDs, annotation-tool version,
and rubric version before adjudication.

## Quality control

Record raw independent labels, disagreement reason, final adjudicated label, adjudicator ID alias,
and timestamp. Report agreement before adjudication (percent agreement and Cohen's kappa where
defined), disagreement counts by category, and all exclusions. A changed source hash invalidates the
label until re-reviewed. Never overwrite prior decisions; append a revision with provenance.

## Acceptance handoff

Export schema-valid JSONL plus a manifest containing counts, split NCT IDs, hashes, seed, annotator
aliases, agreement metrics, adjudication completion, and limitations. `verify_release.py --strict`
must reject Dataset A evidence without this manifest and without complete adjudication.

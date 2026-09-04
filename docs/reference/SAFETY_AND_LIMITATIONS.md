# Safety and Limitations

TRIAL-OPT is a research prototype for clinical-trial **pre-screening** with public and synthetic
data. It does not diagnose disease, provide medical or treatment advice, determine final
eligibility, predict benefit, or replace a qualified clinical-trial team.

## What proof means—and does not mean

A verified proof means that a displayed hard criterion verdict can be replayed from the exact
versioned fact, public protocol source hash, bounded AST, deterministic evaluator, and verifier
rules. It demonstrates software traceability and internal consistency for that artifact.

It does not establish that the input is medically true or complete, that protocol compilation is
clinically exhaustive, that a trial is safe or suitable, that recruitment is current, or that a
person will be enrolled. A score is not a probability. `PRE_SCREEN_PASS` is not “eligible.”

## Enforced boundaries

- Enter only public or synthetic data. Do not upload real medical records, names, dates of birth,
  addresses, contact details, account numbers, or other identifiers.
- The system does not infer a diagnosis from symptoms or imaging. Grade-H retrieval hypotheses are
  inadmissible for eligibility proof.
- Missing information remains unknown; absence of text is not negative evidence.
- It requests only an existing record/value and does not recommend a new test, medication change,
  treatment change, or treatment discontinuation.
- Material protocol language outside the bounded AST becomes `OPAQUE`, blocks hard conclusions,
  and is surfaced for clinician review.
- Every result requires confirmation against the current full protocol and patient context by the
  recruiting trial team.

## Evaluation limitations

The committed quantitative artifact is a project-generated, one-trial S004 structured engineering
smoke. It does not satisfy the mandatory multi-trial Dataset A evaluation and is not a held-out
clinical study. Disease-domain coverage is limited, synthetic missingness cannot capture real chart
quality, and generated structured truth cannot establish clinical accuracy or generalization.

Both false positives and false negatives are possible through source incompleteness, extraction or
compilation errors, retrieval misses, stale recruitment status, unsupported criteria, unusual units,
and patient answers that are incomplete or misunderstood. Conservative blocking reduces unsupported
hard claims but cannot eliminate these risks.

## Staleness and operations

ClinicalTrials.gov content and API behavior, model behavior and availability, prices, software
dependencies, and deployment configuration can change. Snapshot hashes prove byte identity, not
freshness. Live results can degrade to a reviewed snapshot, but that snapshot must display its date.
This prototype is not a HIPAA/compliance system and intentionally rejects real-patient use.

Report security or safety issues through the repository's GitHub issue tracker or private security
advisory using [SECURITY.md](../../.github/SECURITY.md). Never include patient data, secrets, or exploit details in
a public report. No personal email address is published by default.

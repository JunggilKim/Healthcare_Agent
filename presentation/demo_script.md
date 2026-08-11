# TRIAL-OPT Demo Script

## 0:00–0:30 — Problem and boundary

Open Snapshot Demo, point to the public/synthetic-data disclaimer, and state: TRIAL-OPT does not
guess eligibility from an incomplete vignette. It acquires one existing value at a time and carries
replayable evidence for every hard verdict. It is research pre-screening, not diagnosis or final
eligibility.

## 0:30–1:20 — S004 initial analysis

Select S004. Show the role-separated stages and the evidence firewall: bladder-wall imaging may
retrieve a relevant trial, but the disease hypothesis cannot satisfy histology. Open the top trial,
show `UNKNOWN` histology and the proof panel, then replay the proof.

## 1:20–2:10 — Active acquisition and reevaluation

Show that TRIAL-OPT deduplicates unresolved criteria into one pathology slot and selects it by the
fixed utility function. Submit the pinned pathology answer. Show the affected criteria, deterministic
ranking/decision change, request ID, and proof replay. Do not call the score a probability.

## 2:10–2:40 — Research evidence

Open Experiment Evidence. Describe the B0–B6/A1–A8 pipeline and curves, then explicitly state that
the currently committed one-trial result is a non-clinical engineering smoke and cannot support the
mandatory Dataset A superiority claim until the reviewed corpus is complete.

## 2:40–3:10 — Resilience

Enable the permitted dependency-failure rehearsal. Show degraded/snapshot state, bounded retry,
continued completion, and the offline runbook. Export the JSON report without exposing a session
token.

## 3:10–3:30 — Close

Summarize the contribution: active evidence acquisition, proof-carrying deterministic verdicts, and
an evidence firewall. Reiterate trial-team confirmation and current-protocol review.

## Rehearsal record

Record three complete runs before release: normal snapshot, network-disabled snapshot, and failure
rehearsal. Capture date, commit, snapshot hash, viewport, browser, durations, and pass/fail in the
release evidence. Do not check these off without an actual run.

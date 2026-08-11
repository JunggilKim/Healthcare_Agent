# Data Sources

The organizer-provided S001–S010 synthetic cases are preserved verbatim in
`data/seeds/synthetic-patients.json`. They are input seeds, not labeled clinical ground truth.

The Phase-2 S004 retrieval fixture under `data/fixtures/retrieval/S004` was captured from the
official ClinicalTrials.gov API v2 (`apiVersion=2.0.5`, registry data timestamp
`2026-08-11T09:00:06`). Its manifest binds the exact response bytes and the complete
NCT05239624 record by SHA-256. The complete record hash is
`6e342382edc485fd43d567e5b5029b7f4bff550354698199af4320bb6d034532`. The fixture is a
pinned historical source artifact, is not an endorsement by the U.S. National Library of
Medicine, and remains subject to the
[ClinicalTrials.gov Terms and Conditions](https://clinicaltrials.gov/about-site/terms-conditions).
It is not relicensed under this repository's MIT license.

`embeddings.json` is explicitly labeled as a deterministic recorded test fixture. It is not a
Gemini-produced vector corpus and is used only to verify adapter and RRF behavior without a paid
external call. The final demo snapshot will replace this test-only artifact with the
specification-required reviewed corpus and model-produced embeddings.

TREC 2022 retrieval is not yet run.

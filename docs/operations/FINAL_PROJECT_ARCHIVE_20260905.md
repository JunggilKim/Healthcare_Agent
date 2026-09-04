# Final Project Archive

This document preserves the final public evidence for TRIAL-OPT after the competition service was
decommissioned. It does not change the research and clinical-validation limits documented in
`PENDING_EXTERNAL_VALIDATION.md`.

## Competition result

- Event: Healthcare Agentic AI Challenge 2026
- Project: TRIAL-OPT
- Result: 우수상 (3위)
- Result source: project team confirmation
- Archive date: 2026-09-05

## Reproducible source

- Repository: `https://github.com/JunggilKim/Healthcare_Agent`
- Default branch: `main`
- Final archival release tag: `archive-2026-09-05`
- Offline demo: Snapshot Mode using the committed S004 fixture
- Main validation workflow: `.github/workflows/ci.yml`

The GitHub release stores the original submitted ZIP and final presentation outside the Git tree.
This keeps the source checkout small while preserving the exact competition artifacts.

| Artifact | SHA-256 |
| --- | --- |
| `팀평면벡터_최종결과물.zip` | `ea7d24134ed091d996cca26f97bd17dc5a68e1902bfed9c154a1077e4bfc36b7` |
| `TRIAL-OPT-final.pdf` | `16178c212665ab02fd144b8440c61ec0e0f3f0099da791964ec4c68984f72427` |

The ZIP is retained byte-for-byte as submitted, including operating-system metadata. Use the GitHub
repository rather than the ZIP for normal source browsing.

## Final public deployment record

| Field | Historical value |
| --- | --- |
| Platform | Google Cloud Run |
| Region | `asia-northeast3` |
| Service | `trial-opt-web` |
| Final revision | `trial-opt-web-00036-rft` |
| Source commit | `8a9c3006a3d1ba7eaef8e999cd255c18bbd40198` |
| Image digest | `sha256:8bf798f2a8641ca8c2c0a68beea0cf573d5f62637f922dbcc58d486ef1af831f` |
| Historical URL | `https://trial-opt-web-ubvr3b22dq-du.a.run.app` |
| Decommissioned | 2026-09-05 |

The historical URL is intentionally unavailable after decommissioning. Employment or portfolio
review should use the source, CI history, release artifacts, and local Snapshot Mode instead of
expecting a permanently hosted demo.

## Decommissioned project resources

Only resources named for TRIAL-OPT were in scope. The Google Cloud project itself and unrelated
AI Studio resources were not deleted.

- Cloud Run service `trial-opt-web`
- Artifact Registry Docker repository `trial-opt`
- Cloud Storage bucket ending in `-trial-opt-artifacts`
- Secret Manager secrets `trial-opt-session-hmac-salt` and `trial-opt-ip-hash-salt`
- Service account `trial-opt-runtime`
- TRIAL-OPT Firestore application data

Cloud Build history and the shared Cloud Build source bucket are platform-level records and were
left in place. No secret values, access tokens, or user medical data are included in this archive.

## Claim boundary

The award confirms the competition result, not clinical validation. TRIAL-OPT remains a research
prototype built and evaluated with public and synthetic data. It does not diagnose disease,
determine final trial eligibility, or replace qualified clinical review. Metrics marked
`acceptance_eligible=false` remain engineering-smoke evidence rather than external validation.

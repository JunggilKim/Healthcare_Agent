# TRIAL-OPT Clinical Intelligence Demo design attribution

Figma design: <https://www.figma.com/design/byqO1zIjXzQdiLi6bsdF0I>

The implementation does not copy template screens or import template assets. It reinterprets the following Figma Community design principles for TRIAL-OPT. The Community pages identified the resources as CC BY 4.0 when reviewed on 2026-08-27.

| Community resource | Principle used | Source |
| --- | --- | --- |
| Healthcare Research Dashboard | Researcher/clinician information structure, AI analysis, evidence source, validation state | <https://www.figma.com/community/file/1588483873639861741/healthcare-research-dashboard> |
| Enterprise Clinical Trial Dashboard | Desktop clinical-trial workspace, navigation, filters, status hierarchy | <https://www.figma.com/community/file/1569799756671865005/enterprise-clinical-trial-dashboard> |
| Healthcare Monitoring Dashboard UI Kit | Auto Layout, Variants, reusable modules, Tailwind-friendly tokens | <https://www.figma.com/community/file/1502281460845437920/healthcare-monitoring-dashboard-ui-kit> |

License: [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).

All TRIAL-OPT product copy, layouts, components, and code in this repository were created specifically for this demo. Official NCT identifiers, trial titles, registry criterion text, citations, verifier codes, and standard medical terminology remain source data and are not translated or shortened.

## Design file structure

| Figma page | Deliverable |
| --- | --- |
| `00 Cover` | Product framing and safety scope |
| `01 Foundations` | Light clinical color, typography, spacing, radius, elevation, and state tokens |
| `02 Components` | Button, badge, field, tabs, trial card, pipeline stage, criterion table, evidence inspector, graph/chart container, and dialog variants |
| `03 Desktop Screens` | 1440×900 Landing, Trial Workspace, Research Evidence, and Experiment Evidence screens (`10:2`–`10:5`) |
| `04 Mobile & States` | 390×844 Landing and Workspace (`15:2`, `15:3`) plus loading/error/degraded/fallback/empty board (`15:4`) |
| `05 Mapping & Attribution` | Figma-to-code mapping, source roles, license, and attribution (`20:2`) |

The file contains 19 primitive variables, 18 semantic variables, 13 dimension variables, nine Noto Sans KR text styles, and two elevation styles. Components use Auto Layout and variant properties rather than detached screen-only copies.

## Figma-to-code mapping

| Design component or screen | React/CSS implementation | Preserved contract |
| --- | --- | --- |
| Landing / Pre-screen | `frontend/src/app.tsx`, `.landing-*`, `.pre-screen-card` | Snapshot/Live, organizer seed, free text, consent and identifier warning |
| Trial Workspace | `frontend/src/app.tsx`, `.workspace-*` | Session restoration, replay, export, reset, delete, partial results |
| Pipeline Stage | `frontend/src/components/AgentTimeline.tsx` | Exact seven backend stage keys and states |
| Trial Card | `frontend/src/components/TrialCard.tsx` | Official title, NCT ID, rank, raw decision code, non-probabilistic score |
| Criterion Table / Evidence Inspector | `frontend/src/components/CriterionMatrix.tsx`, `ClinicalUI.tsx` | Registry quote, source hash, AST/derivation, proof verdict, evidence grade, verifier and missing slot |
| Next Question | `frontend/src/components/QuestionPanel.tsx` | Exact answer payload, unknown/decline branches, no-new-test warning |
| Proof Graph | `frontend/src/components/ProofGraph.tsx` | Filtered source→fact→criterion→verdict→decision→rank path |
| Retrieval Candidates | `frontend/src/components/RetrievalCandidates.tsx` | 20 retained candidates, top-eight compilation selection and opaque state |
| Research Evidence | `frontend/src/components/ResearcherView.tsx` | Facts, conflicts, hypotheses, source provenance and firewall semantics |
| Experiment Evidence | `frontend/src/components/ExperimentEvidence.tsx` | Committed evaluation artifact and provisional/non-acceptance caveats |
| Runtime states | `frontend/src/app.tsx`, `ClinicalUI.tsx`, `.runtime-*` | loading, error, degraded, fallback and empty without discarding partial proof |
| Design tokens | `frontend/src/styles/index.css` | Navy/blue/teal clinical palette; amber/rose restricted to warning/error/risk |

## Korean display glossary and source preservation

Display translations live in `frontend/src/lib/locale.ts`; backend enums, IDs, API schema, cache keys, prompts, ranking logic, and export content remain unchanged.

| Raw code/concept | Korean-first display |
| --- | --- |
| `POTENTIAL_MATCH` | `잠재적 적합 · POTENTIAL_MATCH` |
| `UNKNOWN` | `확인 필요 · UNKNOWN` |
| `CONFLICT` | `근거 충돌 · CONFLICT` |
| `DEGRADED` | `성능 저하 · DEGRADED` |
| `FALLBACK` | `대체 경로 · FALLBACK` |
| Snapshot Demo | `스냅샷 데모 (Snapshot Demo)` |
| Live Mode | `라이브 모드 (Live Mode)` |

NCT identifiers, official trial titles, registry criterion quotes, citations, verifier codes, standard medical terms, patient source text, and exports retain their English source form. Criterion rows show a concise Korean explanation first while the exact registry source is available through `영어 원문` disclosure, including its source hash and derivation metadata.

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

The 2026-08-27 readability refinement also reviewed the user's duplicated Community files `Snow Dashboard UI Kit`, `Metrix SaaS Dashboard UI Kit`, and several website wireframe/start-up kits. They were used only as visual references for workspace density, card hierarchy, and responsive information order. No nodes, raster assets, icons, or source components were copied from those files, and their licenses are therefore not relied upon by this implementation.

The web app self-hosts the Korean subset of Pretendard 1.3.9 in Regular, Medium, SemiBold, and Bold weights. Pretendard is distributed under the SIL Open Font License 1.1; the bundled license is preserved at `frontend/public/fonts/Pretendard-LICENSE.txt`. The connected Figma editing environment does not expose Pretendard, so Figma retains Noto Sans KR with the same size/weight/line-height hierarchy while production code uses Pretendard.

## Design file structure

| Figma V2 page | Deliverable |
| --- | --- |
| `Components V2` | Status Badge, Command Button, Timeline Stage, Metric Tile, Criterion Row, Retrieval Candidate Row, Evidence Node, Trial Intelligence Hero, Chart Container, Confirm Dialog, App Navigation, and Command Bar (`26:27`) |
| `Desktop V2` | 1440×900 Landing (`40:2`), Trial Workspace (`43:2`), Research Evidence (`46:48`), and Experiment Evidence (`49:94`) |
| `Mobile & States V2` | Loading/error/degraded/fallback/empty gallery (`54:2`), 390px Landing (`57:2`), and Workspace (`60:2`) |
| `05 Mapping & Attribution` | Figma-to-code mapping, source roles, license, and attribution retained alongside the V2 pages |
| `01 Foundations` · `V3 / UI Kit Integration & Web Typography` | UI-kit synthesis, Pretendard-to-Noto mapping, and 12/14/16px readability minimums (`65:2`) |
| `02 Components · V2` · `V3 / Clinical Section Header` | Token-bound reusable section heading with editable eyebrow, title, and source action (`66:2`) |
| `03 Desktop Screens · V2` · V3 screens | 1440×960 Landing / Pre-screen (`70:144`) and Trial Workspace (`67:140`) used for the final implementation pass |

The V2 system contains 67 variables, 14 Noto Sans KR text styles, and four effect styles. Components use Auto Layout, reusable component properties, and semantic aliases rather than detached screen-only copies. The legacy first-pass pages remain archived in the same file for design-history comparison.

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
| App Navigation / Command Bar | `frontend/src/app.tsx`, `.clinical-sidebar`, `.workspace-command-bar`, `.workspace-toolbar` | active workspace context plus replay, export, reset and delete commands |
| Metric Tile / Chart Container | `frontend/src/components/ExperimentEvidence.tsx`, `.metric-chip`, `.experiment-chart` | committed artifact provenance and provisional/non-acceptance labeling |
| Confirm Dialog | `frontend/src/app.tsx`, `.dialog-*` | destructive reset/delete intent and keyboard focus behavior |
| Design tokens | `frontend/src/styles/index.css` | Navy/blue/teal clinical palette; amber/rose restricted to warning/error/risk |

Direct Figma Code Connect publishing was attempted on the V2 component page, but the connected Figma account does not have the required Organization/Enterprise Dev or Full seat. The mapping table above and the `05 Mapping & Attribution` Figma page are therefore the current mapping source of truth; no unpublished or unverifiable Code Connect registration is claimed.

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

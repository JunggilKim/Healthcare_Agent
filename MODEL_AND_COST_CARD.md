# Model and Cost Card

## Frozen routing

| Model | Tasks | Default | Fallback boundary |
|---|---|---:|---|
| `gemini-3.6-flash` | patient extraction, protocol compilation, semantic review, answer interpretation | yes | recorded exact-hash cache or conservative failure; never silently changes verdict policy |
| `gemini-3.5-flash-lite` | retrieval query generation, Korean question/report rendering, noncritical formatting | yes | deterministic template |
| `gemini-embedding-001` | 768-dimensional dense retrieval vectors | yes | BM25-only lexical degradation |

Only first-party Google Cloud model endpoints with Application Default Credentials are allowed.
Google AI Studio billing and API keys are not used. This keeps IAM, logging, regional controls, and
budget accounting under the approved challenge GCP project. The committed offline demo performs no
model call.

Deterministic code—not any model—controls eligibility verdicts, proof replay, ranking, acquisition
utility, question deduplication, stop rules, and safety invariants.

## Lifecycle and limits

The IDs above are specification-frozen. Current project availability, lifecycle dates, quota, and
structured-output compatibility have **not yet been confirmed** in an authorized GCP project. The
release verifier therefore requires a dated human acknowledgement and model-access smoke record.
Task-specific input/output reservations, structured schema limits, retry counts, and fallback rules
are versioned in `config/models.yaml`; requests exceeding their reservation fail or degrade rather
than borrowing unbounded capacity. External calls use bounded timeouts and at most the configured
retry count. Critical compilation allows one repair pass and otherwise remains unverified/opaque.

## Price and cost assumptions

Pricing assumptions are frozen in `config/pricing.yaml` with effective date `2026-08-11` and source
<https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing>. They are planning
inputs, not a current price guarantee or invoice proof.

| Item | Assumption |
|---|---:|
| `gemini-3.6-flash` standard input / output-reasoning | $1.50 / $7.50 per 1M tokens |
| `gemini-3.5-flash-lite` standard input / output-reasoning | $0.30 / $2.50 per 1M tokens |
| `gemini-embedding-001` online input | $0.00015 per 1K tokens |
| Normal cold-session target | $0.45 |
| Hard reservation cap per session | $1.25 |
| Development / demo daily caps | $10 / $25 |
| Application tracked cap | $180 |

The committed evaluation uses deterministic recorded fixtures and therefore has no measured paid
model average. A real per-session average may only be added from sanitized usage metadata generated
by the exact release configuration. `scripts/estimate_cost.py` calculates reservations from the
dated file; `verify_release.py --strict` blocks stale prices older than 14 days without an explicit,
dated acknowledgement.

## $300 challenge allocation

| Category | Planned maximum |
|---|---:|
| Online Gemini development and live tests | $60 |
| Batch compilation, synthetic generation, evaluation | $40 |
| Embeddings | $5 |
| Cloud Run, Firestore, GCS, Artifact Registry, logging | $15 |
| Presentation-day operation | $10 |
| Technical contingency | $70 |
| Untouched reserve | **$100** |
| Total | **$300** |

The operational plan is capped at $200 and the $100 reserve is deliberately untouched. Within that
operational envelope, the application tracks a stricter $180 total model-cost cap. The separate
$200 Cloud Billing budget emits alerts at 25%, 50%, 75%, 90%, and 100%; alerts are notifications,
not hard stops.
- Priority PayGo, quota increases, paid account activation, and unbounded automatic retries are not
  allowed by the release configuration.

These are caps and allocations, not commitments to spend. Billing alerts do not themselves stop
spend; application-side reservations and operator shutdown remain required.

## Known failure modes

- hallucinated or omitted facts, polarity/scope errors, numeric/temporal mistakes;
- schema-invalid or truncated output and provider refusal/timeout;
- embedding drift, language sensitivity, and retrieval misses;
- fluent explanations inconsistent with deterministic verdicts;
- model version, availability, quota, or price drift.

Mitigations include exact spans and hashes, untrusted-data delimiters, schema validation, independent
review, deterministic proof verification, `OPAQUE` abstention, fixed retry/repair limits, recorded
snapshot fallback, and rejection of explanations that contradict authoritative decisions.

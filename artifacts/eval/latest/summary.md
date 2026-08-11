# TRIAL-OPT Evaluation Summary

**Status: PROVISIONAL ENGINEERING SMOKE — NOT ACCEPTANCE ELIGIBLE**

Claim scope: `project-created S004 structured fixture engineering smoke`. This is not clinical validation.

## Generated metrics

| Metric | Value |
|---|---:|
| `criterion_macro_f1_self_consistency` | 0.8 |
| `unsupported_hard_decision_rate_fixture` | 0.0 |
| `false_pre_screen_pass_rate_fixture` | 0.0 |
| `retrieval_recall_at_20_proxy` | 1.0 |
| `b6_final_decision_accuracy_fixture` | 1.0 |
| `b6_median_questions_fixture` | 0.0 |

## Blocking reasons

- Dataset A reviewed corpus and annotations are incomplete.
- The fixture contains one trial, so stable top-3 claims are not estimable.
- Paid LLM baselines B5, P0, and P1 were not run.

Run IDs are recorded in `metrics.json`.

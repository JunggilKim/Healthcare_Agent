import { z } from "zod";

export const proofSchema = z
  .object({
    criterion_id: z.string(),
    criterion_source_hash: z.string(),
    final_verdict: z.enum(["PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE", "CONFLICT"]),
    evidence_fact_ids: z.array(z.string()),
    missing_slot_ids: z.array(z.string()),
    hard_decision_allowed: z.boolean(),
    derivation_steps: z.array(
      z.object({ step_id: z.string(), operation: z.string(), output: z.unknown() }).passthrough(),
    ),
    verifier_checks: z.array(
      z.object({ check_id: z.string(), applicable: z.boolean(), passed: z.boolean() }).passthrough(),
    ),
  })
  .passthrough();

export const questionCandidateSchema = z
  .object({
    question_id: z.string(),
    slot_id: z.string(),
    action: z.string(),
    affected: z.array(z.object({ criterion_id: z.string(), nct_id: z.string() }).passthrough()),
    branches: z
      .array(
        z
          .object({
            branch_id: z.string(),
            label: z.string(),
            response_kind: z.string(),
          })
          .passthrough(),
      )
      .default([]),
    utility_components: z
      .object({ mean_risk_reduction: z.number(), final_utility: z.number() })
      .passthrough()
      .nullable(),
  })
  .passthrough();

export const questionSelectionSchema = z
  .object({
    selected: questionCandidateSchema.nullable(),
    patient_facing_question: z.string().nullable(),
    deterministic_rationale: z.string(),
    top_alternatives: z.array(questionCandidateSchema),
  })
  .passthrough();

export const sessionSchema = z
  .object({
    session_id: z.string(),
    state: z.string(),
    mode: z.string(),
    degradation_codes: z.array(z.string()).default([]),
    export_available: z.boolean().default(true),
    durable_replay: z.boolean().default(true),
    patient_text: z.string(),
    patient_state_version: z.number(),
    facts: z.array(
      z.object({ fact_id: z.string(), slot_id: z.string(), grade: z.string() }).passthrough(),
    ),
    retrieval_hypotheses: z.array(z.object({ concept: z.string(), grade: z.literal("H") }).passthrough()),
    proofs: z.array(proofSchema),
    criteria: z.array(
      z.object({
        criterion_id: z.string(),
        source_direction: z.string(),
        source_quote: z.string(),
        normalized_summary: z.string(),
        ast: z.object({ root_node_id: z.string(), nodes: z.array(z.unknown()) }).passthrough(),
      }),
    ),
    trial_evaluation: z
      .object({
        nct_id: z.string(),
        decision: z.string(),
        display_score: z.number(),
        proof_completeness: z.number(),
      })
      .passthrough()
      .nullable(),
    top_trial: z
      .object({
        nct_id: z.string(),
        title: z.string(),
        overall_status: z.string(),
        data_timestamp: z.string().nullable(),
      })
      .nullable()
      .optional(),
    retrieval: z.unknown().optional(),
    current_question: questionSelectionSchema.nullable(),
  })
  .passthrough();

export type SessionView = z.infer<typeof sessionSchema>;

export const retrievalSchema = z.object({
  mode: z.enum(["live", "snapshot", "hybrid_degraded"]),
  api_version: z.string(),
  registry_data_timestamp: z.string(),
  dense_source_used: z.boolean(),
  degradation_codes: z.array(z.string()),
  selected_for_compilation: z.array(z.string()).max(8),
  ranked_candidates: z
    .array(
      z.object({
        nct_id: z.string(),
        retrieval_score: z.number(),
        exact_condition_match: z.boolean(),
        compiled: z.boolean(),
        compilation_status: z.enum(["NOT_COMPILED", "OPAQUE_REVIEW_REQUIRED", "VERIFIED"]),
        trial: z.object({
          brief_title: z.string(),
          overall_status: z.string(),
          conditions: z.array(z.string()),
        }),
      }),
    )
    .max(20),
});

export type RetrievalView = z.infer<typeof retrievalSchema>;

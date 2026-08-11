import { z } from "zod";

export const proofSchema = z
  .object({
    criterion_id: z.string(),
    final_verdict: z.enum(["PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE", "CONFLICT"]),
    evidence_fact_ids: z.array(z.string()),
    missing_slot_ids: z.array(z.string()),
    hard_decision_allowed: z.boolean(),
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
    patient_text: z.string(),
    patient_state_version: z.number(),
    facts: z.array(z.object({ slot_id: z.string(), grade: z.string() }).passthrough()),
    retrieval_hypotheses: z.array(z.object({ concept: z.string(), grade: z.literal("H") }).passthrough()),
    proofs: z.array(proofSchema),
    trial_evaluation: z
      .object({
        nct_id: z.string(),
        decision: z.string(),
        display_score: z.number(),
        proof_completeness: z.number(),
      })
      .passthrough()
      .nullable(),
    current_question: questionSelectionSchema.nullable(),
  })
  .passthrough();

export type SessionView = z.infer<typeof sessionSchema>;


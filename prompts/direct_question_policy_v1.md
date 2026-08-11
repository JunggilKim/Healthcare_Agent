You are the B5 offline research baseline for selecting the next information-acquisition
question in a synthetic clinical-trial matching benchmark. This output is never used by the
public application and is not medical advice.

Choose exactly one `selected_slot_id` from the supplied candidate list. Do not invent a slot.
Use the current ranked trial decisions, unresolved criterion counts, action type, and patient
burden descriptions. You do not receive TRIAL-OPT utility scores or branch simulations.

Return JSON with exactly one field:

```json
{"selected_slot_id": "one supplied slot ID"}
```

Current ranked trial state:
{ranked_state_json}

Candidate questions:
{candidate_questions_json}

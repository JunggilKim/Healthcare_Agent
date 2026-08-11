# Safety and Limitations

TRIAL-OPT is a research prototype for clinical-trial pre-screening using public and synthetic data. It does not diagnose disease, provide medical advice, determine final eligibility, or replace review by a qualified clinical-trial team.

## What a replayable proof means

A verified evidence trail demonstrates only that a displayed criterion verdict is reproducible from the current versioned synthetic/public patient facts, the pinned public protocol source, the compiled bounded AST, and deterministic evaluator rules. It does not prove medical truth, clinical appropriateness, trial benefit, or eventual enrollment.

## Safety boundaries

- Only public or synthetic data may be entered. Never upload real medical records or personal identifiers.
- Retrieval hypotheses cannot be used as hard eligibility evidence.
- Missing information is not interpreted as an absent condition.
- Unsupported protocol language is retained as `OPAQUE` and forces review where material.
- The application never recommends obtaining a new test, changing treatment, or stopping medication.
- `PRE_SCREEN_PASS` is a conservative research pre-screening state, not final eligibility.

## Known limitations

The bounded protocol language cannot formalize every criterion. The release evaluation covers a limited synthetic benchmark and selected disease domains; it is not clinical validation. Public trial records, APIs, model behavior, and prices can become stale. Both false-positive and false-negative pre-screening results remain possible, so a trial team must confirm every result against current source records and the complete patient context.

Security or safety problems should be reported through the repository issue tracker following [SECURITY.md](SECURITY.md). Do not include real patient data in a report.


# Local cleanup record — 2026-08-13

This record separates the retained release deliverables from local, regenerable
working data removed after the final implementation run.

## Retained deliverables

- `data/demo/current/`: validated demo snapshot used by the deployed revision.
- `artifacts/release/`: deployment, production-smoke, performance, and release
  verification evidence.
- `artifacts/submission/trial-opt-provisional-067ba6f/`: the latest 20-file
  provisional bundle, including the source archive and dependency locks.
- `artifacts/eval/latest/`: the current deterministic evaluation report and
  presentation figures.
- `.venv/`, `node_modules/`, `frontend/node_modules/`, and
  `backend/app/static/`: retained so the checked-out application remains locally
  runnable without reinstalling or rebuilding dependencies immediately.

The submission bundle remains explicitly marked `PROVISIONAL_NOT_SUBMITTABLE`.
The strict release gate still depends on the two human reviewers' completed
annotations/evaluations and the release tag; cleanup does not change that status.

## Recoverably removed from the workspace

The following 173 MB was moved to
`/Users/kimjunggil/.Trash/Healthcare_Agent_cleanup_20260813_final`:

- interrupted and superseded acquisition/compilation pools from
  `Healthcare_Agent_recovery_20260813`;
- `.local_store/`, including live-model compilation caches and local object
  stores;
- superseded provisional bundles `trial-opt-provisional-8bc0075` and
  `trial-opt-provisional-9151d9d`;
- Hypothesis, mypy, pytest, Ruff, Python bytecode, and Playwright result caches;
- generated root `synthetic-patients.json` and workspace `.DS_Store` files.

These files can be restored from the stated Trash folder until the macOS Trash
is emptied. The live-model cache is recoverable there if another compilation run
must avoid repeating paid model calls.

## Post-cleanup verification

- The tracked Git tree was clean and synchronized with `origin/main` before this
  record was added.
- Snapshot validation and package-manifest hash verification were rerun after the
  move.
- No live model calls, production writes, deployment changes, or release tags
  were performed as part of cleanup.

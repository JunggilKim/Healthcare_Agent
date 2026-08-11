# TRIAL-OPT Demo Runbook

## Demonstration boundary

The primary 4–6 minute flow uses the frozen S004 Snapshot Demo only. It makes no outbound call,
incurs no live model cost, does not diagnose the patient, and does not determine final trial
eligibility. `NCT05239624` is a specification-pinned proof slice; the retrieval top 8 remain visibly
opaque until the externally reviewed full corpus is frozen.

## Preflight

Run from the repository root:

```bash
make bootstrap
make lint
make typecheck
make test
npm run e2e --workspace frontend
test ! -f data/demo/current/manifest.json || \
  uv run python scripts/verify_snapshot.py data/demo/current --strict
make demo-offline
```

Open `http://127.0.0.1:8080`. Confirm the header shows `SNAPSHOT DEMO`, the pinned data timestamp,
`MODEL · CACHED / $0.000`, and no degraded badge. Do not select Live Mode in the primary flow.

## 4–6 minute primary flow

| Time | Action | What to say |
|---:|---|---|
| 0:00–0:20 | State the problem on the landing page. | Incomplete descriptions tempt matching systems to guess or ask too many questions. |
| 0:20–0:40 | Select S004 and start the Snapshot analysis. | This is synthetic organizer data and a hash-frozen offline path. |
| 0:40–1:10 | Point to the seven fixed agent stages. | The UI exposes structured stage state, not hidden chain-of-thought. |
| 1:10–2:10 | Open the histology criterion with `Why?`; show the exact registry text, source hash, unknown verdict, and firewall. | CT imaging creates a Grade-H retrieval hypothesis but cannot prove pathology or enter a hard eligibility verdict. |
| 2:10–3:10 | Open Researcher View. Show uniform branches, utility, affected criterion count, and selected pathology action. | The question is selected by deterministic expected risk reduction and fixed tie-breaking. |
| 3:10–4:10 | Choose branch A. Show histology becomes PASS while muscle invasion remains UNKNOWN and becomes the next action. | The interpreter updates only the selected slot and does not infer invasion from histology. |
| 4:10–4:40 | Run `Replay Proof`. | Replay is deterministic, uses no model call, and validates the proof packet against the frozen artifacts. |
| 4:40–5:25 | Open Experiment Evidence. | Only finalized evaluation JSON is displayed; the browser never calculates research metrics. Do not claim pending metrics as results. |
| 5:25–5:50 | Point to Snapshot/data/model/cost badges. | The reliable primary flow remains offline and truthfully labeled. |

## Rehearsal-only failure flow

Start the app at `http://127.0.0.1:8080/?demo-tools=1`. Toggle one dependency failure at a time.
Confirm that the affected stage reads `대체 경로`, the degraded badge appears, and the banner says that
partial results are preserved. The toggle changes presentation state only; it is not a production
remote fault-injection API.

## Safe answer paths

- Branch A confirms only `pathology.histology`; `pathology.muscle_invasion` stays unknown.
- Branch B marks the pathology record unavailable and never repeats the same question.
- `잘 모르겠습니다` and `이 기록을 제공할 수 없습니다` always remain available and lead to the next useful
  action or a final report.
- An answer outside the frozen branches must be rejected as snapshot-unavailable; never improvise
  an offline result.

## Recovery

1. If the page is stale after a build, stop Uvicorn, run `npm run build`, restart `make demo-offline`,
   and reload the page.
2. If a session URL cannot be recovered, return to `/` and create a fresh S004 snapshot session.
3. If a snapshot hash check fails, do not demo it. Restore the reviewed frozen bundle and rerun
   `scripts/verify_snapshot.py`.
4. If Live Mode health is not fully green, remain in Snapshot Mode. A live refresh is optional only
   after the golden flow and with at least 30 seconds remaining.

## Pending external validation

The final three-case full snapshot, live Gemini/embedding calls, GCP persistence/deployment, and
Docker image digest remain pending until the required user-controlled credentials, billing/quota,
clinical review artifacts, and Docker storage are available. See `docs/PENDING_EXTERNAL_VALIDATION.md`.

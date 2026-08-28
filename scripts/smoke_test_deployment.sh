#!/usr/bin/env bash
set -euo pipefail

BASE_URL=""
LIVE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url) BASE_URL="${2:?missing URL}"; shift 2 ;;
    --live) LIVE=1; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$BASE_URL" ]] || { echo "Usage: $0 --base-url URL [--live]" >&2; exit 2; }
BASE_URL="${BASE_URL%/}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

request() {
  local label="$1" method="$2" url="$3" output="$4"; shift 4
  local headers="$TMP_DIR/${label}.headers"
  local timing
  timing="$(curl --silent --show-error --fail-with-body --max-time 300 -D "$headers" -o "$output" -X "$method" "$url" "$@" -w '%{http_code} %{time_total}')"
  local request_id
  request_id="$(awk 'BEGIN{IGNORECASE=1} /^x-request-id:/ {gsub("\r", "", $2); print $2}' "$headers" | tail -1)"
  echo "${label}: HTTP ${timing%% *}, latency ${timing#* }s, request_id=${request_id:-missing}"
}

request health GET "$BASE_URL/api/v1/health" "$TMP_DIR/health.json"
request config GET "$BASE_URL/api/v1/config/public" "$TMP_DIR/config.json"
request cases GET "$BASE_URL/api/v1/demo/cases" "$TMP_DIR/cases.json"

cat >"$TMP_DIR/create.json" <<'JSON'
{"mode":"snapshot","seed_case_id":"S004","evaluation_date":"2026-08-11","language":"en","confirm_synthetic_public":false,"identifier_warning_acknowledged":false}
JSON
request create POST "$BASE_URL/api/v1/sessions" "$TMP_DIR/created.json" -H 'Content-Type: application/json' --data-binary "@$TMP_DIR/create.json"
SESSION_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["session_id"])' "$TMP_DIR/created.json")"
SESSION_TOKEN="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["session_token"])' "$TMP_DIR/created.json")"
AUTH=(-H "X-Session-Token: ${SESSION_TOKEN}")

request analysis POST "$BASE_URL/api/v1/sessions/$SESSION_ID/analysis" "$TMP_DIR/analysis.sse" -H 'Accept: text/event-stream' "${AUTH[@]}"
grep -q '^event: completed' "$TMP_DIR/analysis.sse"
request session GET "$BASE_URL/api/v1/sessions/$SESSION_ID" "$TMP_DIR/session.json" "${AUTH[@]}"
QUESTION_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["current_question"]["selected"]["question_id"])' "$TMP_DIR/session.json")"
request proof GET "$BASE_URL/api/v1/sessions/$SESSION_ID/trials/NCT05239624/proof" "$TMP_DIR/proof.json" "${AUTH[@]}"

python3 - "$QUESTION_ID" >"$TMP_DIR/answer.json" <<'PY'
import json, sys
print(json.dumps({"question_id": sys.argv[1], "answer_text": "Existing pathology report confirms high-grade urothelial carcinoma.", "structured_value": None, "unknown": False, "declined": False}))
PY
request answer POST "$BASE_URL/api/v1/sessions/$SESSION_ID/answers" "$TMP_DIR/answer.sse" -H 'Content-Type: application/json' -H 'Accept: text/event-stream' "${AUTH[@]}" --data-binary "@$TMP_DIR/answer.json"
grep -q '^event: completed' "$TMP_DIR/answer.sse"
request updated GET "$BASE_URL/api/v1/sessions/$SESSION_ID" "$TMP_DIR/updated.json" "${AUTH[@]}"
python3 - "$TMP_DIR/session.json" "$TMP_DIR/updated.json" <<'PY'
import json, sys
before, after = (json.load(open(path)) for path in sys.argv[1:])
assert after["patient_state_version"] > before["patient_state_version"]
assert after["trial_evaluation"] != before["trial_evaluation"]
PY
request proof_after_answer GET "$BASE_URL/api/v1/sessions/$SESSION_ID/trials/NCT05239624/proof" "$TMP_DIR/proof-after-answer.json" "${AUTH[@]}"
python3 - "$TMP_DIR/proof-after-answer.json" <<'PY'
import json, sys
replay = json.load(open(sys.argv[1]))
assert replay["patient_state_version"] == 1
assert replay["replay_executed"] is True
assert replay["replay_method"] == "DETERMINISTIC_EVALUATOR_CURRENT_SESSION"
assert replay["replay_passed"] is True
assert len(replay["replay_results"]) == len(replay["proof_packets"]) == 7
histology = next(
    item for item in replay["replay_results"]
    if item["criterion_id"] == "NCT05239624:INCLUSION:002:5f52ab88"
)
assert histology["patient_state_version"] == 1
assert histology["passed"] is True
PY
python3 - "$TMP_DIR/updated.json" >"$TMP_DIR/snapshot-answer-2.json" <<'PY'
import json, sys
session = json.load(open(sys.argv[1]))
question = session["current_question"]["selected"]
branch = next(
    item for item in question["branches"]
    if item.get("synthetic_value") == {"kind": "boolean", "value": True}
)
print(json.dumps({
    "question_id": question["question_id"],
    "answer_text": None,
    "structured_value": branch["synthetic_value"],
    "unknown": False,
    "declined": False,
}))
PY
request snapshot_answer_2 POST "$BASE_URL/api/v1/sessions/$SESSION_ID/answers" "$TMP_DIR/snapshot-answer-2.sse" -H 'Content-Type: application/json' -H 'Accept: text/event-stream' "${AUTH[@]}" --data-binary "@$TMP_DIR/snapshot-answer-2.json"
grep -q '^event: completed' "$TMP_DIR/snapshot-answer-2.sse"
! grep -q 'SNAPSHOT_BRANCH_UNAVAILABLE' "$TMP_DIR/snapshot-answer-2.sse"
request snapshot_completed GET "$BASE_URL/api/v1/sessions/$SESSION_ID" "$TMP_DIR/snapshot-completed.json" "${AUTH[@]}"
python3 - "$TMP_DIR/snapshot-completed.json" <<'PY'
import json, sys
session = json.load(open(sys.argv[1]))
selection = session["current_question"]
assert session["state"] == "COMPLETE"
assert selection.get("selected") is None
assert selection.get("stop_reason") == "SNAPSHOT_BRANCH_COVERAGE_EXHAUSTED"
PY
request export GET "$BASE_URL/api/v1/sessions/$SESSION_ID/export.json" "$TMP_DIR/export.json" "${AUTH[@]}"

if [[ "$LIVE" -eq 1 ]]; then
  sed 's/"snapshot"/"live"/' "$TMP_DIR/create.json" >"$TMP_DIR/live-create.json"
  request live_create POST "$BASE_URL/api/v1/sessions" "$TMP_DIR/live-created.json" -H 'Content-Type: application/json' --data-binary "@$TMP_DIR/live-create.json"
  LIVE_SESSION_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["session_id"])' "$TMP_DIR/live-created.json")"
  LIVE_SESSION_TOKEN="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["session_token"])' "$TMP_DIR/live-created.json")"
  request live_analysis POST "$BASE_URL/api/v1/sessions/$LIVE_SESSION_ID/analysis" "$TMP_DIR/live-analysis.sse" -H 'Accept: text/event-stream' -H "X-Session-Token: ${LIVE_SESSION_TOKEN}"
  grep -q '^event: completed' "$TMP_DIR/live-analysis.sse"
  request live_session GET "$BASE_URL/api/v1/sessions/$LIVE_SESSION_ID" "$TMP_DIR/live-session.json" -H "X-Session-Token: ${LIVE_SESSION_TOKEN}"
  python3 - "$TMP_DIR/live-session.json" <<'PY'
import json, sys
session = json.load(open(sys.argv[1]))
ranked_ids = session.get("full_state", {}).get("aggregate", {}).get("ranked_nct_ids", [])
selected_ids = session.get("retrieval", {}).get("selected_for_compilation", [])
degradation_codes = session.get("degradation_codes", [])
question_selection = session.get("current_question", {})
selected_question = question_selection.get("selected")
stop_reason = question_selection.get("stop_reason")
assert ranked_ids, "Live analysis completed without any ranked trials"
assert 1 <= len(selected_ids) <= 4, "Live compilation budget was not enforced"
top_id = ranked_ids[0]
aggregate = session["full_state"]["aggregate"]
top_evaluation = aggregate["trial_evaluations"][top_id]
top_compiled = aggregate["compiled_trials"][top_id]
assert top_compiled["protocol_verified"], "Top Live trial protocol is not fully verified"
assert not top_evaluation.get("degradation_codes"), "Top Live trial used a degraded compilation"
assert top_evaluation["decision"] != "REVIEW_REQUIRED", "Top Live trial requires manual review"
assert top_evaluation["proof_completeness"] >= 0.9, "Top Live proof completeness is below 90%"
assert selected_question, (
    f"S004 Live did not select a usable next question (stop_reason={stop_reason})"
)
assert "LIVE_RETRIEVAL_TIMEOUT_SNAPSHOT_USED" not in degradation_codes, (
    "Live registry retrieval timed out and silently became a snapshot analysis"
)
print(
    "live_result: "
    f"mode={session.get('mode')}, ranked={len(ranked_ids)}, "
    f"compiled={len(selected_ids)}, "
    f"next_action={selected_question.get('question_id') if selected_question else stop_reason}, "
    f"degradations={','.join(degradation_codes) or 'none'}"
)
PY
  python3 - "$TMP_DIR/live-session.json" >"$TMP_DIR/live-answer.json" <<'PY'
import json, sys
session = json.load(open(sys.argv[1]))
question = session["current_question"]["selected"]
branch = next(
    (item for item in question.get("branches", []) if item.get("response_kind") == "VALUE"),
    None,
)
assert branch and branch.get("synthetic_value") is not None, (
    "Live next question has no typed answer branch"
)
print(json.dumps({
    "question_id": question["question_id"],
    "answer_text": None,
    "structured_value": branch["synthetic_value"],
    "unknown": False,
    "declined": False,
}))
PY
  request live_answer POST "$BASE_URL/api/v1/sessions/$LIVE_SESSION_ID/answers" "$TMP_DIR/live-answer.sse" -H 'Content-Type: application/json' -H 'Accept: text/event-stream' -H "X-Session-Token: ${LIVE_SESSION_TOKEN}" -H 'Idempotency-Key: production-live-smoke-turn-1' --data-binary "@$TMP_DIR/live-answer.json"
  grep -q '^event: completed' "$TMP_DIR/live-answer.sse"
  request live_updated GET "$BASE_URL/api/v1/sessions/$LIVE_SESSION_ID" "$TMP_DIR/live-updated.json" -H "X-Session-Token: ${LIVE_SESSION_TOKEN}"
  python3 - "$TMP_DIR/live-session.json" "$TMP_DIR/live-updated.json" <<'PY'
import json, sys
before, after = (json.load(open(path)) for path in sys.argv[1:])
before_question = before["current_question"]["selected"]["question_id"]
after_selected = (after.get("current_question") or {}).get("selected")
assert after["patient_state_version"] > before["patient_state_version"]
assert after_selected is not None, "Live answer stopped before selecting the next question"
assert after_selected["question_id"] != before_question, (
    "Live answer did not advance beyond the submitted question"
)
PY
  unset LIVE_SESSION_TOKEN
  echo "Exactly one explicit Live Mode session creation and analysis was issued."
fi

unset SESSION_TOKEN AUTH
echo "Production smoke passed; session token was not printed and temporary files were removed."

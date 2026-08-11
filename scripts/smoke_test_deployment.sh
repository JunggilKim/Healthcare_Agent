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
request export GET "$BASE_URL/api/v1/sessions/$SESSION_ID/export.json" "$TMP_DIR/export.json" "${AUTH[@]}"

if [[ "$LIVE" -eq 1 ]]; then
  sed 's/"snapshot"/"live"/' "$TMP_DIR/create.json" >"$TMP_DIR/live-create.json"
  request live_create POST "$BASE_URL/api/v1/sessions" "$TMP_DIR/live-created.json" -H 'Content-Type: application/json' --data-binary "@$TMP_DIR/live-create.json"
  LIVE_SESSION_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["session_id"])' "$TMP_DIR/live-created.json")"
  LIVE_SESSION_TOKEN="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["session_token"])' "$TMP_DIR/live-created.json")"
  request live_analysis POST "$BASE_URL/api/v1/sessions/$LIVE_SESSION_ID/analysis" "$TMP_DIR/live-analysis.sse" -H 'Accept: text/event-stream' -H "X-Session-Token: ${LIVE_SESSION_TOKEN}"
  grep -q '^event: completed' "$TMP_DIR/live-analysis.sse"
  unset LIVE_SESSION_TOKEN
  echo "Exactly one explicit Live Mode session creation and analysis was issued."
fi

unset SESSION_TOKEN AUTH
echo "Production smoke passed; session token was not printed and temporary files were removed."

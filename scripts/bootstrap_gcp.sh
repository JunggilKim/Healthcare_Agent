#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
BILLING_ACCOUNT_ID=""
REGION="asia-northeast3"
FIRESTORE_LOCATION="asia-northeast3"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="${2:?missing project}"; shift 2 ;;
    --billing-account) BILLING_ACCOUNT_ID="${2:?missing billing account}"; shift 2 ;;
    --region) REGION="${2:?missing region}"; shift 2 ;;
    --firestore-location) FIRESTORE_LOCATION="${2:?missing Firestore location}"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$PROJECT_ID" && -n "$BILLING_ACCOUNT_ID" ]] || {
  echo "Usage: $0 --project PROJECT --billing-account ACCOUNT [--region asia-northeast3] [--firestore-location asia-northeast3]" >&2
  exit 2
}
[[ "$REGION" == "asia-northeast3" && "$FIRESTORE_LOCATION" == "asia-northeast3" ]] || {
  echo "The frozen architecture requires asia-northeast3." >&2
  exit 2
}
command -v gcloud >/dev/null || { echo "gcloud is required" >&2; exit 1; }

BUCKET="${PROJECT_ID}-trial-opt-artifacts"
RUNTIME_SA="trial-opt-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
BUILD_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
APIS=(
  run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com
  firestore.googleapis.com storage.googleapis.com aiplatform.googleapis.com
  secretmanager.googleapis.com logging.googleapis.com monitoring.googleapis.com
)
SECRETS=(trial-opt-session-hmac-salt trial-opt-ip-hash-salt)

gcloud config set project "$PROJECT_ID" >/dev/null
ACTUAL_BILLING="$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingAccountName)' 2>/dev/null || true)"
if [[ "$ACTUAL_BILLING" != "billingAccounts/${BILLING_ACCOUNT_ID}" ]]; then
  echo "Project is not linked to the supplied billing account. Link and verify Free Trial eligibility manually; this script will not change billing." >&2
  exit 1
fi
echo "Billing link verified. Confirm Welcome Credit / Free Trial status in Cloud Billing before continuing."

gcloud services enable "${APIS[@]}" --project "$PROJECT_ID"

if ! gcloud artifacts repositories describe trial-opt --location "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud artifacts repositories create trial-opt --repository-format=docker --location "$REGION" --project "$PROJECT_ID" --description="TRIAL-OPT challenge images"
fi
gcloud artifacts repositories add-iam-policy-binding trial-opt \
  --location "$REGION" --project "$PROJECT_ID" \
  --member="serviceAccount:${BUILD_SA}" --role=roles/artifactregistry.writer >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${BUILD_SA}" --role=roles/logging.logWriter \
  --condition=None >/dev/null

if ! gcloud storage buckets describe "gs://${BUCKET}" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${BUCKET}" --project "$PROJECT_ID" --location "$REGION" --uniform-bucket-level-access --public-access-prevention
fi
gcloud storage buckets update "gs://${BUCKET}" --uniform-bucket-level-access --public-access-prevention
LIFECYCLE_FILE="$(mktemp)"
trap 'rm -f "$LIFECYCLE_FILE"' EXIT
printf '%s\n' '{"rule":[{"action":{"type":"Delete"},"condition":{"age":7,"matchesPrefix":["sessions/"]}}]}' >"$LIFECYCLE_FILE"
gcloud storage buckets update "gs://${BUCKET}" --lifecycle-file="$LIFECYCLE_FILE"

if ! gcloud firestore databases describe --database='(default)' --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud firestore databases create --database='(default)' --location="$FIRESTORE_LOCATION" --type=firestore-native --project "$PROJECT_ID"
fi

if ! gcloud iam service-accounts describe "$RUNTIME_SA" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create trial-opt-runtime --display-name="TRIAL-OPT runtime" --project "$PROJECT_ID"
fi

for ROLE in roles/aiplatform.user roles/datastore.user roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:${RUNTIME_SA}" --role="$ROLE" --condition=None >/dev/null
done
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" --member="serviceAccount:${RUNTIME_SA}" --role=roles/storage.objectUser >/dev/null

for SECRET in "${SECRETS[@]}"; do
  if ! gcloud secrets describe "$SECRET" --project "$PROJECT_ID" >/dev/null 2>&1; then
    gcloud secrets create "$SECRET" --replication-policy=automatic --project "$PROJECT_ID"
  fi
  ENABLED_VERSION="$(gcloud secrets versions list "$SECRET" --project "$PROJECT_ID" --filter='state=ENABLED' --limit=1 --format='value(name)')"
  if [[ -z "$ENABLED_VERSION" ]]; then
    openssl rand -base64 48 | tr -d '\n' | \
      gcloud secrets versions add "$SECRET" --data-file=- --project "$PROJECT_ID" >/dev/null
    echo "Created the first enabled version for ${SECRET}; value was not printed."
  fi
  gcloud secrets add-iam-policy-binding "$SECRET" --project "$PROJECT_ID" --member="serviceAccount:${RUNTIME_SA}" --role=roles/secretmanager.secretAccessor >/dev/null
done

echo "Bootstrap complete without service-account keys, quota changes, or paid-account activation."
echo "Model smoke (one minimal call per frozen model): ALLOW_LIVE_MODEL_CALLS=true uv run python scripts/validate_model_access.py --project ${PROJECT_ID}"
echo "Budget alert: create a USD 200 project budget with 25%, 50%, 75%, 90%, and 100% actual/forecast notifications in Cloud Billing if your identity cannot automate billing budgets."

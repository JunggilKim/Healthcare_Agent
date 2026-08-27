#!/usr/bin/env bash
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
GCP_REGION="${GCP_REGION:-asia-northeast3}"
MIN_INSTANCES="${MIN_INSTANCES:-1}"
SNAPSHOT_VERSION="${SNAPSHOT_VERSION:?Set SNAPSHOT_VERSION to the verified manifest version}"
[[ "$GCP_REGION" == "asia-northeast3" ]] || { echo "GCP_REGION must be asia-northeast3" >&2; exit 2; }
[[ "$MIN_INSTANCES" =~ ^[0-9]+$ ]] || { echo "MIN_INSTANCES must be a non-negative integer" >&2; exit 2; }
command -v gcloud >/dev/null || { echo "gcloud is required" >&2; exit 1; }

uv run python scripts/validate_snapshot.py data/demo/current --strict
MANIFEST_VERSION="$(uv run python -c 'import json; print(json.load(open("data/demo/current/manifest.json"))["snapshot_version"])')"
[[ "$SNAPSHOT_VERSION" == "$MANIFEST_VERSION" ]] || { echo "SNAPSHOT_VERSION does not match manifest" >&2; exit 1; }

GIT_SHA="$(git rev-parse HEAD)"
[[ -z "$(git status --porcelain)" ]] || { echo "Refusing to deploy a dirty worktree" >&2; exit 1; }
IMAGE="${GCP_REGION}-docker.pkg.dev/${PROJECT_ID}/trial-opt/trial-opt:${GIT_SHA}"

make test
make frontend-build
gcloud builds submit --project "$PROJECT_ID" --tag "$IMAGE"
gcloud run deploy trial-opt-web \
  --project "$PROJECT_ID" \
  --image "$IMAGE" \
  --region "$GCP_REGION" \
  --service-account "trial-opt-runtime@${PROJECT_ID}.iam.gserviceaccount.com" \
  --cpu 2 --memory 2Gi --concurrency 4 --timeout 300 \
  --min-instances "$MIN_INSTANCES" --max-instances 2 --allow-unauthenticated \
  --set-env-vars "APP_ENV=prod,APP_VERSION=${GIT_SHA},STORE_BACKEND=gcp,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=global,GCP_REGION=${GCP_REGION},GCS_BUCKET=${PROJECT_ID}-trial-opt-artifacts,DEFAULT_RUNTIME_MODE=snapshot,DEMO_SNAPSHOT_VERSION=${SNAPSHOT_VERSION},APP_ENABLE_FAULT_INJECTION=false,ALLOW_LIVE_MODEL_CALLS=true,ALLOW_LIVE_CTGOV_CALLS=true" \
  --set-secrets "SESSION_TOKEN_HMAC_SALT=trial-opt-session-hmac-salt:latest,IP_HASH_SALT=trial-opt-ip-hash-salt:latest"

SERVICE_URL="$(gcloud run services describe trial-opt-web --project "$PROJECT_ID" --region "$GCP_REGION" --format='value(status.url)')"
DIGEST="$(gcloud artifacts docker images describe "$IMAGE" --project "$PROJECT_ID" --format='value(image_summary.digest)')"
mkdir -p artifacts/release
printf '%s\n' "$DIGEST" > artifacts/release/IMAGE_DIGEST.txt
printf '%s\n' "$SERVICE_URL" > artifacts/release/PRODUCTION_URL.txt
echo "Deployed ${GIT_SHA} as ${DIGEST} to ${SERVICE_URL}"
echo "Run: ./scripts/smoke_test_deployment.sh --base-url '${SERVICE_URL}'"

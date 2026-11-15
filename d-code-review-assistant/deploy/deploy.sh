#!/usr/bin/env bash
# Deploy the review service to Cloud Run. UNVERIFIED: gcloud is not installed on the author's machine and the
# project is free-tier only, so this script has never been run. Read it before running it.
#
#   PROJECT=my-project REGION=europe-west1 ./deploy/deploy.sh
#
# Prerequisites: gcloud authenticated, billing enabled on PROJECT (Cloud Run itself has a free tier), and two secrets in
# Secret Manager: GOOGLE_API_KEY (a Gemini API key) and CODEREVIEW_SERVER_TOKEN (any long random string).
set -euo pipefail

: "${PROJECT:?set PROJECT}"
REGION="${REGION:-europe-west1}"
SERVICE="${SERVICE:-code-review-assistant}"

gcloud services enable run.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com --project "$PROJECT"

# --no-allow-unauthenticated: callers need an IAM identity AND the bearer token the app checks.
# --concurrency 2 / --max-instances: each request is an LLM run, keep cost and rate limits bounded.
# CODEREVIEW_SANDBOX=off: the service never executes submitted code (see the Dockerfile comment). Reviews are static
# (AST + style + LLM); to run tests, send the sandbox step to an isolated executor instead.
gcloud run deploy "$SERVICE" \
  --project "$PROJECT" --region "$REGION" --source . \
  --no-allow-unauthenticated \
  --memory 1Gi --cpu 1 --concurrency 2 --max-instances 3 --timeout 300 \
  --set-env-vars "CODEREVIEW_SANDBOX=off,CODEREVIEW_PROVIDER=gemini,GOOGLE_GENAI_USE_VERTEXAI=FALSE" \
  --set-secrets "GOOGLE_API_KEY=GOOGLE_API_KEY:latest,CODEREVIEW_SERVER_TOKEN=CODEREVIEW_SERVER_TOKEN:latest"

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format 'value(status.url)')"
echo "Deployed: $URL"
echo "Try (Cloud Run checks the identity token in X-Serverless-Authorization; the app checks its own bearer token):"
echo "  curl -s $URL/healthz -H \"X-Serverless-Authorization: Bearer \$(gcloud auth print-identity-token)\""

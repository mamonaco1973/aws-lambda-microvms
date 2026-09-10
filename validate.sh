#!/bin/bash
# ==============================================================================
# validate.sh — MicroVM Suspend/Resume Validation
# ------------------------------------------------------------------------------
# Purpose:
#   - Launch a MicroVM from the deployed image.
#   - Seed live interpreter state (a variable, a generator, a file).
#   - Suspend the MicroVM and confirm AWS reports SUSPENDED.
#   - Resume it with an ordinary HTTPS request and prove the state survived.
#   - Terminate the validation session and print the quick-start URLs.
#
# Fast-Fail Behavior:
#   - Script exits immediately on command failure, unset variables,
#     or failed pipelines.
#
# Requirements:
#   - curl, jq, Terraform and AWS CLI installed and authenticated.
#   - Terraform deployment completed successfully.
# ==============================================================================
set -euo pipefail
export AWS_DEFAULT_REGION="us-east-1"
cd "$(dirname "$0")"

IMAGE_ARN=$(terraform   -chdir=01-microvms output -raw image_arn 2>/dev/null || true)
APP_URL=$(terraform     -chdir=02-lambdas  output -raw web_url   2>/dev/null || true)
API_BASE=$(terraform    -chdir=02-lambdas  output -raw api_url   2>/dev/null || true)

if [ -z "${IMAGE_ARN}" ] || [ -z "${APP_URL}" ] || [ -z "${API_BASE}" ]; then
  echo "ERROR: Could not read Terraform outputs. Run ./apply.sh first."
  exit 1
fi

# ------------------------------------------------------------------------------
# Helper: poll until the MicroVM reaches the requested lifecycle state
# ------------------------------------------------------------------------------
wait_for_state() {
  local vm_id="$1" desired="$2" attempt
  for ((attempt = 1; attempt <= 60; attempt++)); do
    state=$(aws lambda-microvms get-microvm --microvm-identifier "${vm_id}" \
      --query "state" --output text)
    if [[ "${state}" == "${desired}" ]]; then
      return 0
    fi
    sleep 1
  done
  echo "ERROR: MicroVM ${vm_id} never reached ${desired} (last state: ${state})."
  exit 1
}

# ------------------------------------------------------------------------------
# Helper: authenticated request to the MicroVM endpoint, with resume retries
# ------------------------------------------------------------------------------
# Never use curl -f here. Under set -e it aborts the whole script on any HTTP
# error, the EXIT trap terminates the VM, and the failure is reported as
# silence. Capture the status instead and say what happened.
#
# Lambda answers 502 while a suspended MicroVM is still being restored, so a
# resume-triggering request has to tolerate it rather than treat it as fatal.
vm_request() {
  local method="$1" path="$2" data="${3:-}"
  local attempt response status body

  for ((attempt = 1; attempt <= 10; attempt++)); do
    if [[ -n "${data}" ]]; then
      response=$(curl -s -w $'\n%{http_code}' --max-time 60 -X "${method}" \
        "https://${ENDPOINT}${path}" \
        -H "X-aws-proxy-auth: ${TOKEN}" -H "X-aws-proxy-port: 8080" \
        -H "Content-Type: application/json" -d "${data}") || response=$'\n000'
    else
      response=$(curl -s -w $'\n%{http_code}' --max-time 60 \
        "https://${ENDPOINT}${path}" \
        -H "X-aws-proxy-auth: ${TOKEN}" -H "X-aws-proxy-port: 8080") || response=$'\n000'
    fi

    status="${response##*$'\n'}"
    body="${response%$'\n'*}"

    if [[ "${status}" == "200" ]]; then
      printf '%s' "${body}"
      return 0
    fi

    # 000 is a curl-level failure (timeout, connection reset), which a resume in
    # progress can also produce. Both are worth another attempt.
    if [[ "${status}" == "502" || "${status}" == "503" || "${status}" == "504" || "${status}" == "000" ]]; then
      echo "NOTE: ${path} returned ${status}; MicroVM still resuming, retry ${attempt}/10..." >&2
      sleep 3
      continue
    fi

    echo "ERROR: ${method} ${path} returned HTTP ${status}" >&2
    echo "ERROR: Response body: ${body}" >&2
    return 1
  done

  echo "ERROR: ${method} ${path} never succeeded (last status ${status})." >&2
  echo "ERROR: Response body: ${body}" >&2
  echo "ERROR: MicroVM state is now: $(aws lambda-microvms get-microvm \
    --microvm-identifier "${VM_ID}" --query state --output text 2>/dev/null || echo unknown)" >&2
  return 1
}

# Terminate the validation MicroVM no matter how the script exits, so a failed
# check can never leave a billable session running.
VM_ID=""
cleanup() {
  if [[ -n "${VM_ID}" ]]; then
    echo "NOTE: Terminating validation session ${VM_ID}..."
    aws lambda-microvms terminate-microvm --microvm-identifier "${VM_ID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# ------------------------------------------------------------------------------
# Step 1: Launch a MicroVM from the deployed image
# ------------------------------------------------------------------------------
echo "NOTE: Launching a validation MicroVM..."

RUN=$(aws lambda-microvms run-microvm \
  --image-identifier "${IMAGE_ARN}" \
  --run-hook-payload '{"tenant":"validate"}' \
  --ingress-network-connectors "arn:aws:lambda:${AWS_DEFAULT_REGION}:aws:network-connector:aws-network-connector:ALL_INGRESS" \
  --idle-policy '{"autoResumeEnabled":true,"maxIdleDurationSeconds":60,"suspendedDurationSeconds":900}' \
  --maximum-duration-in-seconds 1800)

VM_ID=$(echo "${RUN}" | jq -r '.microvmId')
ENDPOINT=$(echo "${RUN}" | jq -r '.endpoint')
wait_for_state "${VM_ID}" "RUNNING"
echo "NOTE: MicroVM ${VM_ID} is RUNNING."

TOKEN=$(aws lambda-microvms create-microvm-auth-token \
  --microvm-identifier "${VM_ID}" \
  --expiration-in-minutes 30 \
  --allowed-ports '[{"port":8080}]' \
  --query 'authToken."X-aws-proxy-auth"' --output text)

# ------------------------------------------------------------------------------
# Step 2: Seed live interpreter state
# ------------------------------------------------------------------------------
echo "NOTE: Seeding interpreter state..."

read -r -d '' SEED_CODE <<'PYTHON' || true
balance = 41
cursor = (n * n for n in range(1000))
next(cursor)
Path("note.txt").write_text("Alice was here")
print("seeded")
PYTHON

SEED=$(jq -n --arg code "${SEED_CODE}" '{code: $code}')

SEEDED=$(vm_request POST /execute "${SEED}") || exit 1
echo "${SEEDED}" | jq -e '.ok == true' >/dev/null || {
  echo "ERROR: Seed cell failed: $(echo "${SEEDED}" | jq -r '.stdout')"
  exit 1
}

NONCE_BEFORE=$(vm_request GET /state | jq -r '.session_nonce')
echo "NOTE: Session nonce before suspend: ${NONCE_BEFORE}"

# ------------------------------------------------------------------------------
# Step 3: Suspend the MicroVM
# ------------------------------------------------------------------------------
echo "NOTE: Suspending the MicroVM..."

aws lambda-microvms suspend-microvm --microvm-identifier "${VM_ID}" >/dev/null
wait_for_state "${VM_ID}" "SUSPENDED"
echo "NOTE: MicroVM is SUSPENDED; compute charges have stopped."

# ------------------------------------------------------------------------------
# Step 4: Resume with ordinary HTTPS traffic and verify surviving state
# ------------------------------------------------------------------------------
echo "NOTE: Sending an HTTPS request to auto-resume the MicroVM..."

read -r -d '' RESUME_CODE <<'PYTHON' || true
balance += 1
print(balance, next(cursor), Path("note.txt").read_text())
PYTHON

RESUMED=$(vm_request POST /execute "$(jq -n --arg code "${RESUME_CODE}" '{code: $code}')") || {
  echo "ERROR: The MicroVM did not resume on incoming traffic."
  exit 1
}

echo "${RESUMED}" | jq -e '.ok == true' >/dev/null || {
  echo "ERROR: Cell failed after resume: $(echo "${RESUMED}" | jq -r '.stdout')"
  exit 1
}

OUTPUT=$(echo "${RESUMED}" | jq -r '.stdout' | tr -d '\r\n')
if [[ "${OUTPUT}" != "42 1 Alice was here" ]]; then
  echo "ERROR: State did not survive suspension. Expected '42 1 Alice was here', got '${OUTPUT}'."
  exit 1
fi
echo "NOTE: Memory, generator position and disk all survived: ${OUTPUT}"

NONCE_AFTER=$(vm_request GET /state | jq -r '.session_nonce')
if [[ "${NONCE_BEFORE}" != "${NONCE_AFTER}" ]]; then
  echo "ERROR: Session nonce changed; this was a fresh VM, not a resumed one."
  exit 1
fi
echo "NOTE: Session nonce unchanged; this is the same interpreter, not a restart."

# ------------------------------------------------------------------------------
# Step 5: Confirm the endpoint rejects an unauthenticated request
# ------------------------------------------------------------------------------
STATUS=$(curl -s -o /dev/null -w '%{http_code}' "https://${ENDPOINT}/state")
if [[ "${STATUS}" != "403" ]]; then
  echo "ERROR: MicroVM endpoint returned ${STATUS} without a token; expected 403."
  exit 1
fi
echo "NOTE: MicroVM endpoint rejects requests with no auth token (403)."

STATUS=$(curl -s -o /dev/null -w '%{http_code}' "${API_BASE}/api/status")
if [[ "${STATUS}" != "401" ]]; then
  echo "ERROR: Controller API returned ${STATUS} without a token; expected 401."
  exit 1
fi
echo "NOTE: Controller API rejects requests with no Cognito access token (401)."

# ------------------------------------------------------------------------------
# Deployment Summary
# ------------------------------------------------------------------------------
echo "NOTE: Create a presenter with ./create_user.sh, then sign in with Cognito."

echo ""
echo "================================================================================="
echo "  Lambda MicroVMs — Deployment validated!"
echo "================================================================================="
echo "  App : ${APP_URL}"
echo "  API : ${API_BASE}"
echo "================================================================================="
echo ""

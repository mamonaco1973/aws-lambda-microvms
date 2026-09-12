#!/bin/bash
# ==============================================================================
# validate.sh — Suspend/Resume validation
# ------------------------------------------------------------------------------
# Purpose:
#   - Launch a MicroVM from the bash image.
#   - Seed live shell state: variables, a function, a file.
#   - Suspend it and confirm AWS reports SUSPENDED.
#   - Resume it with an ordinary HTTPS request and prove the state survived.
#   - Terminate the validation session.
#
# Fast-Fail Behavior:
#   - Exits immediately on command failure, unset variables or failed pipelines.
#
# Requirements:
#   - curl, jq, Terraform and AWS CLI installed and authenticated.
#   - Terraform deployment completed successfully.
# ==============================================================================
set -euo pipefail
export AWS_DEFAULT_REGION="us-east-1"
cd "$(dirname "$0")"

IMAGES=$(terraform    -chdir=01-microvms output -json images          2>/dev/null || true)
APP_URL=$(terraform   -chdir=02-lambdas  output -raw  web_url         2>/dev/null || true)
API_BASE=$(terraform  -chdir=02-lambdas  output -raw  api_url         2>/dev/null || true)
PASSPHRASE=$(terraform -chdir=02-lambdas output -raw  demo_passphrase 2>/dev/null || true)

if [ -z "${IMAGES}" ] || [ -z "${APP_URL}" ] || [ -z "${API_BASE}" ]; then
  echo "ERROR: Could not read Terraform outputs. Run ./apply.sh first."
  exit 1
fi

# The seed/resume pair is written to print exactly this.
EXPECTED="42 1 validated"

# ------------------------------------------------------------------------------
# Helper: poll until the MicroVM reaches the requested lifecycle state
# ------------------------------------------------------------------------------
wait_for_state() {
  local vm_id="$1" desired="$2" attempt state
  for ((attempt = 1; attempt <= 60; attempt++)); do
    state=$(aws lambda-microvms get-microvm --microvm-identifier "${vm_id}" \
      --query "state" --output text)
    [[ "${state}" == "${desired}" ]] && return 0
    sleep 1
  done
  echo "ERROR: MicroVM ${vm_id} never reached ${desired} (last state: ${state})."
  exit 1
}

# ------------------------------------------------------------------------------
# Helper: authenticated request to the MicroVM endpoint, with resume retries
# ------------------------------------------------------------------------------
# Never use curl -f here. Under set -e it aborts the whole script on any HTTP
# error, the trap terminates the VM, and the failure is reported as silence.
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
  return 1
}

# Terminate whatever is running no matter how the script exits, so a failed
# check can never leave a billable session behind.
VM_ID=""
cleanup() {
  if [[ -n "${VM_ID}" ]]; then
    echo "NOTE: Terminating validation session ${VM_ID}..."
    aws lambda-microvms terminate-microvm --microvm-identifier "${VM_ID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# ------------------------------------------------------------------------------
# Per-runtime cells. Written so both print exactly "${EXPECTED}" after a resume.
# ------------------------------------------------------------------------------
seed_code() {
  case "$1" in
    bash) printf '%s' 'balance=41
cursor=0
next_square() { square=$((cursor * cursor)); cursor=$((cursor + 1)); }
next_square
printf %s validated > note.txt
echo seeded' ;;
  esac
}

# ------------------------------------------------------------------------------
# Helper: run one cell to completion
# ------------------------------------------------------------------------------
# /execute submits and returns a job id; the result is collected by polling
# /result/<id>. Echoes the finished result object, so callers read .ok and
# .stdout from it exactly as they did when /execute answered directly.
#
# A submission that never started (busy or dead shell) comes back with no job
# id and already looks like a finished result, so it is passed straight
# through.
run_cell() {
  local submitted job state
  submitted=$(vm_request POST /execute "$(jq -n --arg code "$1" '{code: $code}')") || return 1
  job=$(echo "${submitted}" | jq -r '.job // empty')
  if [[ -z "${job}" ]]; then
    echo "${submitted}"
    return 0
  fi

  # Bounded so a wedged cell fails the run instead of hanging it. Generous
  # because a resume happens on the first poll after a suspend.
  local waited=0
  while (( waited < 300 )); do
    local polled
    polled=$(vm_request GET "/result/${job}") || return 1
    state=$(echo "${polled}" | jq -r '.state')
    if [[ "${state}" == "done" ]]; then
      echo "${polled}" | jq -c '.result'
      return 0
    fi
    if [[ "${state}" == "unknown" ]]; then
      echo '{"ok": false, "stdout": "The MicroVM no longer knows this job."}'
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  echo '{"ok": false, "stdout": "Cell did not finish within 300s."}'
}

resume_code() {
  case "$1" in
    # A function definition surviving the checkpoint is what next_square proves
    # here; the counter it advances is the generator stand-in.
    bash) printf '%s' 'balance=$((balance + 1))
next_square
echo "${balance} ${square} $(cat note.txt)"' ;;
  esac
}

# ==============================================================================
# Validate every runtime
# ==============================================================================
for RUNTIME in $(echo "${IMAGES}" | jq -r 'keys[]'); do
  IMAGE_ARN=$(echo "${IMAGES}" | jq -r --arg r "${RUNTIME}" '.[$r].image_arn')

  echo
  echo "NOTE: ===== Validating the ${RUNTIME} MicroVM ====="
  echo "NOTE: Launching from ${IMAGE_ARN}..."

  RUN=$(aws lambda-microvms run-microvm \
    --image-identifier "${IMAGE_ARN}" \
    --run-hook-payload "{\"runtime\":\"${RUNTIME}\"}" \
    --ingress-network-connectors "arn:aws:lambda:${AWS_DEFAULT_REGION}:aws:network-connector:aws-network-connector:ALL_INGRESS" \
    --idle-policy '{"autoResumeEnabled":true,"maxIdleDurationSeconds":60,"suspendedDurationSeconds":900}' \
    --maximum-duration-in-seconds 1800)
  # Deliberately tighter than the controller's policy in handler.py. This
  # session lives about a minute and suspends explicitly, so the idle timer
  # never matters; the short ceiling is a backstop that reclaims the VM
  # quickly if this script is killed before the cleanup trap runs.

  VM_ID=$(echo "${RUN}" | jq -r '.microvmId')
  ENDPOINT=$(echo "${RUN}" | jq -r '.endpoint')
  wait_for_state "${VM_ID}" "RUNNING"
  echo "NOTE: MicroVM ${VM_ID} is RUNNING."

  TOKEN=$(aws lambda-microvms create-microvm-auth-token \
    --microvm-identifier "${VM_ID}" \
    --expiration-in-minutes 30 \
    --allowed-ports '[{"port":8080}]' \
    --query 'authToken."X-aws-proxy-auth"' --output text)

  # ---- Seed live interpreter state ------------------------------------------
  echo "NOTE: Seeding interpreter state..."
  SEEDED=$(run_cell "$(seed_code "${RUNTIME}")") || exit 1
  echo "${SEEDED}" | jq -e '.ok == true' >/dev/null || {
    echo "ERROR: Seed cell failed: $(echo "${SEEDED}" | jq -r '.stdout')"
    exit 1
  }

  NONCE_BEFORE=$(vm_request GET /state | jq -r '.session_nonce')
  echo "NOTE: Session nonce before suspend: ${NONCE_BEFORE}"

  # ---- Suspend ---------------------------------------------------------------
  echo "NOTE: Suspending the MicroVM..."
  aws lambda-microvms suspend-microvm --microvm-identifier "${VM_ID}" >/dev/null
  wait_for_state "${VM_ID}" "SUSPENDED"
  echo "NOTE: MicroVM is SUSPENDED; compute charges have stopped."

  # ---- Resume with ordinary traffic and verify surviving state ---------------
  echo "NOTE: Sending an HTTPS request to auto-resume the MicroVM..."
  RESUMED=$(run_cell "$(resume_code "${RUNTIME}")") || {
    echo "ERROR: The ${RUNTIME} MicroVM did not resume on incoming traffic."
    exit 1
  }
  echo "${RESUMED}" | jq -e '.ok == true' >/dev/null || {
    echo "ERROR: Cell failed after resume: $(echo "${RESUMED}" | jq -r '.stdout')"
    exit 1
  }

  OUTPUT=$(echo "${RESUMED}" | jq -r '.stdout' | tr -d '\r\n')
  if [[ "${OUTPUT}" != "${EXPECTED}" ]]; then
    echo "ERROR: ${RUNTIME} state did not survive suspension."
    echo "ERROR: Expected '${EXPECTED}', got '${OUTPUT}'."
    exit 1
  fi
  echo "NOTE: Memory, generator position and disk all survived: ${OUTPUT}"

  NONCE_AFTER=$(vm_request GET /state | jq -r '.session_nonce')
  if [[ "${NONCE_BEFORE}" != "${NONCE_AFTER}" ]]; then
    echo "ERROR: Session nonce changed; this was a fresh VM, not a resumed one."
    exit 1
  fi
  echo "NOTE: Session nonce unchanged; the same interpreter, not a restart."

  # ---- The endpoint refuses an unauthenticated request ------------------------
  STATUS=$(curl -s -o /dev/null -w '%{http_code}' "https://${ENDPOINT}/state")
  if [[ "${STATUS}" != "403" ]]; then
    echo "ERROR: MicroVM endpoint returned ${STATUS} without a token; expected 403."
    exit 1
  fi
  echo "NOTE: MicroVM endpoint rejects requests with no auth token (403)."

  cleanup
  VM_ID=""
done

# ------------------------------------------------------------------------------
# The controller refuses a request without the demo passphrase
# ------------------------------------------------------------------------------
echo
STATUS=$(curl -s -o /dev/null -w '%{http_code}' "${API_BASE}/api/status")
if [[ "${STATUS}" != "401" ]]; then
  echo "ERROR: Controller API returned ${STATUS} without a passphrase; expected 401."
  exit 1
fi
echo "NOTE: Controller API rejects requests with no demo passphrase (401)."

# ------------------------------------------------------------------------------
# Deployment Summary
# ------------------------------------------------------------------------------
echo ""
echo "================================================================================="
echo "  Lambda MicroVMs — Deployment validated!"
echo "================================================================================="
echo "  App        : ${APP_URL}"
echo "  API        : ${API_BASE}"
echo "  Passphrase : ${PASSPHRASE}"
echo "================================================================================="
echo ""

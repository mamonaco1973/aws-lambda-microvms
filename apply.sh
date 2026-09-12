#!/bin/bash
# ==============================================================================
# File: apply.sh
# ==============================================================================
# Purpose:
#   Deploys the Lambda MicroVM demo in three phases: the pre-initialized MicroVM
#   images, the API/Lambda controller, and the static web frontend.
#
# Notes:
#   - Requires AWS CLI v2 (with lambda-microvms), Terraform, jq, zip, python3.
#   - Lambda builds the ARM64 image remotely; no local Docker daemon is needed.
# ==============================================================================

# ------------------------------------------------------------------------------
# GLOBAL CONFIGURATION
# ------------------------------------------------------------------------------
# Sets the AWS region and enforces strict Bash error handling:
#   -e : Exit immediately on command failure
#   -u : Treat unset variables as errors
#   -o pipefail : Catch errors in piped commands
# ------------------------------------------------------------------------------
export AWS_DEFAULT_REGION="us-east-1"
set -euo pipefail
cd "$(dirname "$0")"

# Must match the runtime in 02-lambdas/lambda.tf; used to resolve vendored wheels.
LAMBDA_PYTHON="3.14"

# ------------------------------------------------------------------------------
# ENVIRONMENT PRE-CHECK
# ------------------------------------------------------------------------------
echo "NOTE: Running environment validation..."
./check_env.sh

# ------------------------------------------------------------------------------
# CLEAR STALE AUTO-LOADED VARIABLE FILES
# ------------------------------------------------------------------------------
# Every phase now receives its inputs through explicit -var flags. Terraform
# still auto-loads any *.auto.tfvars.json left in a phase directory, and those
# are gitignored, so a checkout updated by git pull can keep feeding stale
# values -- including variables that no longer exist -- indefinitely.
# ------------------------------------------------------------------------------
for phase in 01-microvms 02-lambdas 03-webapp; do
  if [[ -f "${phase}/deployment.auto.tfvars.json" ]]; then
    echo "NOTE: Removing stale ${phase}/deployment.auto.tfvars.json"
    rm -f "${phase}/deployment.auto.tfvars.json"
  fi
done

# ------------------------------------------------------------------------------
# PACKAGE THE MICROVM APPLICATIONS
# ------------------------------------------------------------------------------
# One zip per runtime -- a Dockerfile plus its session server. Lambda builds the
# image remotely, so no local Docker daemon is involved.
# ------------------------------------------------------------------------------
echo "NOTE: Packaging the MicroVM applications..."

rm -rf dist && mkdir -p dist
(cd 01-microvms/bash && zip -q -X -r ../../dist/bash-app.zip Dockerfile server.py worker.sh)

# ------------------------------------------------------------------------------
# PACKAGE THE CONTROLLER LAMBDA
# ------------------------------------------------------------------------------
# The Lambda runtime's bundled SDK predates lambda-microvms, so a current boto3
# is vendored into the deployment package.
#
# These wheels target the Lambda runtime, not the build host. AL2023 ships
# Python 3.9 while boto3 needs 3.10+, and older pip applies Requires-Python
# against the running interpreter, so the gate must be disabled explicitly.
# Every dependency here is a pure-Python (py3-none-any) wheel, so the host's
# interpreter and platform genuinely do not matter.
# ------------------------------------------------------------------------------
echo "NOTE: Packaging the controller Lambda..."

rm -rf dist/build && mkdir -p dist/build
python3 -m pip install --quiet --disable-pip-version-check --no-compile \
  --only-binary=:all: --python-version "${LAMBDA_PYTHON}" \
  --ignore-requires-python --no-warn-conflicts \
  --target dist/build "boto3>=1.43.0"

# Confirm the vendored SDK actually carries the MicroVM service model. Skipping
# Requires-Python means a too-old boto3 would otherwise ship silently and the
# controller would fail at runtime with "Unknown service: lambda-microvms".
if [[ ! -d dist/build/botocore/data/lambda-microvms ]]; then
  echo "ERROR: Vendored boto3 has no lambda-microvms service model."
  echo "ERROR: Check the pip index for a boto3 release that supports MicroVMs."
  exit 1
fi
echo "NOTE: Vendored boto3 includes the lambda-microvms service model."

cp 02-lambdas/app/*.py dist/build/
(cd dist/build && zip -q -X -r ../controller.zip . -x '*/__pycache__/*')

# ------------------------------------------------------------------------------
# SELECT THE MANAGED BASE IMAGE
# ------------------------------------------------------------------------------
# BaseImageVersion is required by AWS::Lambda::MicrovmImage, and versions age out
# through DEPRECATED/EXPIRING, so resolve the newest at deploy time rather than
# pinning a number in source. ListManagedMicrovmImageVersions returns only
# createdAt/imageArn/imageVersion -- there is no state field to filter on.
# ------------------------------------------------------------------------------
echo "NOTE: Selecting the newest managed base image version..."

BASE_IMAGE_ARN="arn:aws:lambda:${AWS_DEFAULT_REGION}:aws:microvm-image:al2023-1"
BASE_IMAGE_VERSION=$(aws lambda-microvms list-managed-microvm-image-versions \
  --image-identifier "${BASE_IMAGE_ARN}" \
  --query "sort_by(items, &createdAt)[-1].imageVersion" --output text)

if [[ -z "${BASE_IMAGE_VERSION}" || "${BASE_IMAGE_VERSION}" == "None" ]]; then
  echo "ERROR: No managed base image version found for ${BASE_IMAGE_ARN}."
  echo "ERROR: Raw response follows so the field names can be checked:"
  aws lambda-microvms list-managed-microvm-image-versions \
    --image-identifier "${BASE_IMAGE_ARN}" || true
  exit 1
fi
echo "NOTE: Using base image version ${BASE_IMAGE_VERSION}"

# ------------------------------------------------------------------------------
# BUILD THE MICROVM IMAGES
# ------------------------------------------------------------------------------
echo "NOTE: Building the MicroVM images (this takes several minutes)..."

# Written here and reused verbatim by destroy.sh, so teardown never has to
# re-derive these values. Deliberately NOT named terraform.tfvars or
# *.auto.tfvars: Terraform must never auto-load it, so a stale copy can only
# take effect when a command passes it explicitly.
jq -n --arg region "${AWS_DEFAULT_REGION}" --arg version "${BASE_IMAGE_VERSION}" \
  '{region: $region, base_image_version: $version}' \
  > 01-microvms/deployment.tfvars.json

terraform -chdir=01-microvms init -input=false
terraform -chdir=01-microvms apply -auto-approve -input=false \
  -var-file=deployment.tfvars.json

IMAGES=$(terraform -chdir=01-microvms output -json images)
echo "${IMAGES}" | jq -r 'to_entries[] | "NOTE: " + .key + " image " + .value.image_name + " version " + .value.image_version'

# ------------------------------------------------------------------------------
# BUILD THE HTTP API AND THE CONTROLLER LAMBDA
# ------------------------------------------------------------------------------
echo "NOTE: Deploying the HTTP API and the controller Lambda..."

jq -n --arg region "${AWS_DEFAULT_REGION}" --arg base "${BASE_IMAGE_VERSION}" \
      --argjson images "${IMAGES}" \
  '{region: $region, name: "microvms", base_image_version: $base, images: $images}' \
  > 02-lambdas/deployment.tfvars.json

terraform -chdir=02-lambdas init -input=false
terraform -chdir=02-lambdas apply -auto-approve -input=false \
  -var-file=deployment.tfvars.json

# ------------------------------------------------------------------------------
# BUILD THE WEB APPLICATION
# ------------------------------------------------------------------------------
# config.json carries the API URL and the SPA's public Cognito client id and
# hosted-UI domain. All three are public by design -- a public OAuth client is
# what PKCE exists to make safe -- which is just as well, because config.json
# is world-readable.
# ------------------------------------------------------------------------------
echo "NOTE: Building the web application..."

WEB_BUCKET=$(terraform -chdir=02-lambdas output -raw web_bucket_name)
terraform -chdir=02-lambdas output -json web_config > 03-webapp/config.json

jq -n --arg region "${AWS_DEFAULT_REGION}" --arg bucket "${WEB_BUCKET}" \
  '{region: $region, web_bucket_name: $bucket}' \
  > 03-webapp/deployment.tfvars.json

terraform -chdir=03-webapp init -input=false
terraform -chdir=03-webapp apply -auto-approve -input=false \
  -var-file=deployment.tfvars.json

# ------------------------------------------------------------------------------
# BUILD VALIDATION
# ------------------------------------------------------------------------------
echo "NOTE: Running build validation..."
./validate.sh

# ==============================================================================
# END OF SCRIPT
# ==============================================================================

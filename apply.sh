#!/bin/bash
# ==============================================================================
# File: apply.sh
# ==============================================================================
# Purpose:
#   Deploys the Lambda MicroVM demo in three phases: the pre-initialized MicroVM
#   image, the Cognito/API/Lambda controller, and the static web frontend.
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
# PACKAGE THE MICROVM APPLICATION
# ------------------------------------------------------------------------------
# Lambda builds the image from this zip: a Dockerfile plus the session server.
# ------------------------------------------------------------------------------
echo "NOTE: Packaging the MicroVM application..."

rm -rf dist && mkdir -p dist
(cd 01-microvms/app && zip -q -X -r ../../dist/app.zip Dockerfile server.py worker.py)

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
# BUILD THE MICROVM IMAGE
# ------------------------------------------------------------------------------
echo "NOTE: Building the MicroVM image (this takes several minutes)..."

terraform -chdir=01-microvms init -input=false
terraform -chdir=01-microvms apply -auto-approve \
  -var="region=${AWS_DEFAULT_REGION}" \
  -var="base_image_version=${BASE_IMAGE_VERSION}"

IMAGE_ARN=$(terraform -chdir=01-microvms output -raw image_arn)
IMAGE_VERSION=$(terraform -chdir=01-microvms output -raw image_version)
echo "NOTE: MicroVM image ${IMAGE_ARN} version ${IMAGE_VERSION}"

# ------------------------------------------------------------------------------
# BUILD COGNITO, API GATEWAY AND THE CONTROLLER LAMBDA
# ------------------------------------------------------------------------------
echo "NOTE: Deploying Cognito, API Gateway and the controller Lambda..."

terraform -chdir=02-lambdas init -input=false
terraform -chdir=02-lambdas apply -auto-approve \
  -var="region=${AWS_DEFAULT_REGION}" \
  -var="name=microvms" \
  -var="image_arn=${IMAGE_ARN}" \
  -var="image_version=${IMAGE_VERSION}"

# ------------------------------------------------------------------------------
# BUILD THE WEB APPLICATION
# ------------------------------------------------------------------------------
# config.json carries the Cognito domain, client id and API URL to the browser.
# ------------------------------------------------------------------------------
echo "NOTE: Building the web application..."

WEB_BUCKET=$(terraform -chdir=02-lambdas output -raw web_bucket_name)
terraform -chdir=02-lambdas output -json web_config > 03-webapp/config.json

terraform -chdir=03-webapp init -input=false
terraform -chdir=03-webapp apply -auto-approve \
  -var="region=${AWS_DEFAULT_REGION}" \
  -var="web_bucket_name=${WEB_BUCKET}"

# ------------------------------------------------------------------------------
# BUILD VALIDATION
# ------------------------------------------------------------------------------
echo "NOTE: Running build validation..."
./validate.sh

# ==============================================================================
# END OF SCRIPT
# ==============================================================================

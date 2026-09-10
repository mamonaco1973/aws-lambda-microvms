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
# Resolve wheels for the Lambda runtime's Python, not the build host's. Without
# --python-version, pip filters by the local interpreter: AL2023 ships Python
# 3.9, and boto3 needs 3.10+, so every usable release silently disappears.
# All of these dependencies are pure-Python (py3-none-any) wheels.
# ------------------------------------------------------------------------------
echo "NOTE: Packaging the controller Lambda..."

rm -rf dist/build && mkdir -p dist/build
python3 -m pip install --quiet --disable-pip-version-check --no-compile \
  --only-binary=:all: --python-version "${LAMBDA_PYTHON}" \
  --target dist/build "boto3>=1.43.0"
cp 02-lambdas/app/*.py dist/build/
(cd dist/build && zip -q -X -r ../controller.zip . -x '*/__pycache__/*')

# ------------------------------------------------------------------------------
# SELECT THE MANAGED BASE IMAGE
# ------------------------------------------------------------------------------
# Base image versions age out through DEPRECATED/EXPIRING, so resolve an
# AVAILABLE one at deploy time rather than pinning a number in source.
# ------------------------------------------------------------------------------
echo "NOTE: Selecting an AVAILABLE managed base image version..."

BASE_IMAGE_ARN="arn:aws:lambda:${AWS_DEFAULT_REGION}:aws:microvm-image:al2023-1"
BASE_IMAGE_VERSION=$(aws lambda-microvms list-managed-microvm-image-versions \
  --image-identifier "${BASE_IMAGE_ARN}" \
  --query "items[?state=='AVAILABLE'] | [0].imageVersion" --output text)

if [[ -z "${BASE_IMAGE_VERSION}" || "${BASE_IMAGE_VERSION}" == "None" ]]; then
  echo "ERROR: No AVAILABLE managed base image version found in ${AWS_DEFAULT_REGION}."
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

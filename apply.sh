#!/bin/bash
# ================================================================================
# Build the Lambda MicroVM Demo
# Creates supporting AWS resources and a pre-initialized image with Terraform.
# Runs a real two-session validation and terminates the validation sessions.
# ================================================================================
set -euo pipefail
cd "$(dirname "$0")"
echo "NOTE: Running environment validation..."
./check_env.sh
source ./scripts/common.sh

echo "NOTE: Packaging the MicroVM application..."
"$PYTHON" scripts/lab.py package
echo "NOTE: Selecting the managed base image and writing Terraform variables..."
"$PYTHON" scripts/lab.py write-image-vars
echo "NOTE: Initializing MicroVM Terraform providers..."
terraform -chdir=01-microvms init -input=false

if [[ -f 02-lambdas/terraform.tfstate || -f 02-lambdas/terraform.tfstate.backup ]]; then
  echo "NOTE: Stopping new controller operations and waiting for the worker before updating..."
  terraform -chdir=02-lambdas init -input=false
  "$PYTHON" scripts/cloud.py quiesce
fi
if [[ -f 01-microvms/terraform.tfstate || -f 01-microvms/terraform.tfstate.backup ]]; then
  echo "NOTE: Terminating existing sessions for this image before updating it..."
  "$PYTHON" scripts/lab.py cleanup
else
  echo "NOTE: First deployment; no existing MicroVM sessions to clean up."
fi
echo "NOTE: Building Lambda MicroVM infrastructure..."
terraform -chdir=01-microvms apply -auto-approve
echo "NOTE: Deploying Cognito, API Gateway and Lambda controller..."
"$PYTHON" scripts/cloud.py package
"$PYTHON" scripts/cloud.py prepare-backend
terraform -chdir=02-lambdas init -input=false
terraform -chdir=02-lambdas apply -auto-approve
"$PYTHON" scripts/cloud.py prepare-web
terraform -chdir=03-webapp init -input=false
terraform -chdir=03-webapp apply -auto-approve
"$PYTHON" scripts/cloud.py resume
echo "NOTE: Running build validation..."
./validate.sh
if [[ "$RUN_BROWSER_TESTS" == 1 ]]; then
  ./validate_web.sh
else
  echo "NOTE: Automated Cognito/browser acceptance was not run (RUN_BROWSER_TESTS=0)."
  echo "NOTE: After creating your presenter, complete login and the browser steps in RECORDING.md."
fi
echo "NOTE: Build complete. Run ./demo.sh, then use Cognito Sign up to register and verify your email."

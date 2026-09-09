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

echo "NOTE: Preparing the application artifact and managed base image version..."
"$PYTHON" scripts/cloud.py quiesce
"$PYTHON" scripts/lab.py prepare
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
echo "NOTE: Build complete. Run ./create_user.sh once, then ./demo.sh"

#!/bin/bash
# ================================================================================
# Environment Validation
# Checks tools, creates an isolated Python environment, and verifies AWS access.
# ================================================================================
source "$(dirname "$0")/scripts/common.sh"

echo "NOTE: Validating that required commands are found in your PATH."
for command_name in aws terraform "$PYTHON"; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "ERROR: $command_name is not found in the current PATH."
    exit 1
  fi
  echo "NOTE: $command_name is found in the current PATH."
done
python_version="$("$PYTHON" --version)"
echo "NOTE: $python_version"

if [[ ! -d .venv ]]; then
  echo "NOTE: Creating the project's Python virtual environment..."
  "$PYTHON" -m venv .venv
fi
source "$PROJECT_DIR/scripts/common.sh"
if ! "$PYTHON" -c "import boto3; assert boto3.__version__ == '1.43.90'" 2>/dev/null; then
  echo "NOTE: Installing the project dependencies..."
  "$PYTHON" -m pip install -r requirements.txt | sed 's/^/NOTE: /'
fi

echo "NOTE: Checking the AWS CLI connection using your active AWS credentials."
aws_account="$(aws sts get-caller-identity --query Account --output text)"
echo "NOTE: AWS account: $aws_account"
aws lambda-microvms run-microvm --generate-cli-skeleton input >/dev/null
"$PYTHON" scripts/lab.py doctor
echo "NOTE: Environment validation complete."

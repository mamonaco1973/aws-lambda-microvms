#!/bin/bash
# ==============================================================================
# Environment Validation — required tooling and AWS connectivity
# ==============================================================================
set -euo pipefail

echo "NOTE: Validating that required commands are found in your PATH."
commands=("aws" "terraform" "jq" "zip" "python3")
all_found=true

for cmd in "${commands[@]}"; do
  if ! command -v "$cmd" &> /dev/null; then
    echo "ERROR: $cmd is not found in the current PATH."
    all_found=false
  else
    echo "NOTE: $cmd is found in the current PATH."
  fi
done

if [ "$all_found" = true ]; then
  echo "NOTE: All required commands are available."
else
  echo "ERROR: One or more commands are missing."
  exit 1
fi

echo "NOTE: Checking AWS cli connection."

aws sts get-caller-identity --query "Account" --output text >> /dev/null

if [ $? -ne 0 ]; then
  echo "ERROR: Failed to connect to AWS. Please check your credentials and environment variables."
  exit 1
else
  echo "NOTE: Successfully logged into AWS."
fi

# The MicroVM APIs ship in recent CLI v2 builds only; fail early with a clear
# message instead of an opaque "Invalid choice" halfway through apply.
if ! aws lambda-microvms help >/dev/null 2>&1; then
  echo "ERROR: This AWS CLI does not support 'lambda-microvms'. Upgrade to the latest AWS CLI v2."
  exit 1
fi
echo "NOTE: AWS CLI supports the lambda-microvms service."

# Only pip itself has to run locally. The controller's wheels are resolved for
# the Lambda runtime's Python, so the host interpreter's version is irrelevant --
# but a pip too old to honor --python-version would silently build a bad zip.
if ! python3 -m pip --version >/dev/null 2>&1; then
  echo "ERROR: python3 -m pip is not available. Install pip (dnf install -y python3-pip)."
  exit 1
fi

pip_major="$(python3 -m pip --version | sed -E 's/^pip ([0-9]+).*/\1/')"
if [ "${pip_major}" -lt 20 ]; then
  echo "ERROR: pip ${pip_major} is too old to resolve wheels for another Python version."
  echo "ERROR: Upgrade with: python3 -m pip install --user --upgrade pip"
  exit 1
fi
echo "NOTE: pip $(python3 -m pip --version | awk '{print $2}') can target the Lambda runtime."

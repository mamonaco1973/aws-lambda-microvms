#!/bin/bash
# Shared paths and environment. Source from the root scripts; do not execute.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
export AWS_REGION="$AWS_DEFAULT_REGION"
export AWS_PROFILE="${AWS_PROFILE:-default}"
export AWS_PAGER=""
export AWS_EC2_METADATA_DISABLED=true

if [[ -x .venv/bin/python ]]; then
  PYTHON="$PROJECT_DIR/.venv/bin/python"
elif [[ -x .venv/Scripts/python.exe ]]; then
  PYTHON="$PROJECT_DIR/.venv/Scripts/python.exe"
elif [[ "${OSTYPE:-}" == msys* ]]; then
  PYTHON=python
elif command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
else
  PYTHON=python
fi

# AWS CLI/boto3 ship their own CA bundles. On Windows, honor certificates already
# trusted by Windows (including corporate inspection CAs) without disabling TLS.
if [[ "${OSTYPE:-}" == msys* && -z "${AWS_CA_BUNDLE:-}" ]]; then
  export AWS_CA_BUNDLE
  AWS_CA_BUNDLE="$("$PYTHON" scripts/windows_ca.py)"
fi

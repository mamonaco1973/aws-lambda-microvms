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
elif [[ -n "${PROJECT_PYTHON:-}" ]]; then
  PYTHON="$PROJECT_PYTHON"
elif [[ "${OSTYPE:-}" == msys* ]]; then
  PYTHON=python
elif command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
else
  PYTHON=python
fi

# pip filters out releases that do not support the selected Python interpreter.
# Check before creating a venv or attempting any dependency installation.
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  "$PYTHON" --version >&2 || true
  echo "ERROR: This project requires Python 3.10 or newer; boto3 1.43.90 does not support older Python." >&2
  echo "ERROR: If .venv already exists, move it aside and recreate it with a supported interpreter." >&2
  echo "NOTE: Example after installing Python 3.12: PROJECT_PYTHON=python3.12 ./setup_dev.sh" >&2
  return 1
fi

# AWS CLI/boto3 ship their own CA bundles. On Windows, honor certificates already
# trusted by Windows (including corporate inspection CAs) without disabling TLS.
if [[ "${OSTYPE:-}" == msys* && -z "${AWS_CA_BUNDLE:-}" ]]; then
  export AWS_CA_BUNDLE
  AWS_CA_BUNDLE="$("$PYTHON" scripts/windows_ca.py)"
fi

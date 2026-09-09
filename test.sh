#!/bin/bash
# Local verification only: no AWS resources are created.
source "$(dirname "$0")/scripts/common.sh"
if [[ ! -d .venv ]]; then
  "$PYTHON" -m venv .venv
  source ./scripts/common.sh
fi
"$PYTHON" -m pip install -r requirements-dev.txt
"$PYTHON" -m pytest -q tests
"$PYTHON" scripts/lab.py package
"$PYTHON" scripts/cloud.py package
if [[ "$RUN_BROWSER_TESTS" == 1 ]]; then
  "$PYTHON" tests/browser_smoke.py
else
  echo "NOTE: Browser fixture tests omitted (RUN_BROWSER_TESTS=0); Python and Terraform checks still run."
fi
./validate.sh --local
for phase in 01-microvms 02-lambdas 03-webapp; do
  terraform -chdir="$phase" init -input=false
  terraform -chdir="$phase" fmt -check
  terraform -chdir="$phase" validate
done
terraform -chdir=01-microvms test

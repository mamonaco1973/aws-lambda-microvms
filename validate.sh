#!/bin/bash
# Verify memory, file isolation, authentication, suspension and fresh launch.
# --local runs application checks only and clearly reports omitted AWS checks.
source "$(dirname "$0")/scripts/common.sh"
echo "NOTE: Running Lambda MicroVM demo validation..."
if [[ "${1:-}" != "--local" ]]; then
  "$PYTHON" scripts/cloud.py quiesce
  trap '"$PYTHON" scripts/cloud.py resume' EXIT
  "$PYTHON" scripts/lab.py cleanup
fi
"$PYTHON" scripts/lab.py validate "$@"
if [[ "${1:-}" != "--local" ]]; then
  "$PYTHON" scripts/cloud.py check
  "$PYTHON" scripts/cloud.py resume
  trap - EXIT
fi
echo "NOTE: Validation complete. Test sessions have been terminated."
if [[ "${1:-}" != "--local" ]]; then
  "$PYTHON" scripts/cloud.py url
fi

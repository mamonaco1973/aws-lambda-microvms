#!/bin/bash
# Install Python test dependencies and headless Chromium on the development box.
# On Ubuntu/Debian, Playwright may request sudo to install browser system libraries.
source "$(dirname "$0")/scripts/common.sh"
if [[ ! -d .venv ]]; then
  "$PYTHON" -m venv .venv
  source ./scripts/common.sh
fi
"$PYTHON" -m pip install -r requirements-dev.txt
if [[ "$RUN_BROWSER_TESTS" == 0 ]]; then
  echo "NOTE: Browser installation omitted. Amazon Linux can deploy and run SDK tests without a local browser."
  echo "NOTE: Complete Cognito login and the recording walkthrough in your workstation browser after deployment."
elif [[ "$(uname -s)" == Linux ]]; then
  "$PYTHON" -m playwright install --with-deps chromium
else
  echo "NOTE: Browser tests use installed Edge on Windows; PLAYWRIGHT_CHANNEL overrides the selection."
fi
echo "NOTE: Development dependencies ready. Run ./test.sh, then ./apply.sh."

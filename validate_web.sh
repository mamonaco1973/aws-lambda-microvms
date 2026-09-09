#!/bin/bash
# Live acceptance through Cognito and the AWS-hosted controller. No local server.
# Uses and removes a temporary presenter; terminates this demo's sessions.
source "$(dirname "$0")/scripts/common.sh"
"$PYTHON" -m pip install -r requirements-dev.txt
"$PYTHON" tests/browser_live.py

#!/bin/bash
# Print the AWS-hosted frontend URL. Nothing runs locally to serve the app.
set -euo pipefail
cd "$(dirname "$0")"

echo "NOTE: Application URL: $(terraform -chdir=02-lambdas output -raw web_url)"
echo "NOTE: Sign in with a Cognito user, or create one with ./create_user.sh."

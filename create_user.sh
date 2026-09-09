#!/bin/bash
# ==============================================================================
# Create a Cognito Presenter
# ==============================================================================
# Reads the password with `read -s` so it never reaches shell history, the
# process table or Terraform state. Re-run it to reset an existing password.
# ==============================================================================
set -euo pipefail
export AWS_DEFAULT_REGION="us-east-1"
cd "$(dirname "$0")"

USER_POOL_ID=$(terraform -chdir=02-lambdas output -raw user_pool_id)
WEB_URL=$(terraform -chdir=02-lambdas output -raw web_url)

read -r -p "Presenter email: " EMAIL
read -r -s -p "Password (12+ chars, upper, lower, number): " PASSWORD; echo
read -r -s -p "Confirm password: " CONFIRM; echo

if [[ "${PASSWORD}" != "${CONFIRM}" ]]; then
  echo "ERROR: Passwords do not match."
  exit 1
fi

# MessageAction=SUPPRESS keeps the demo from emailing a real invitation.
if aws cognito-idp admin-create-user \
     --user-pool-id "${USER_POOL_ID}" \
     --username "${EMAIL}" \
     --message-action SUPPRESS \
     --user-attributes Name=email,Value="${EMAIL}" Name=email_verified,Value=true \
     >/dev/null 2>&1; then
  echo "NOTE: Created presenter ${EMAIL}."
else
  echo "NOTE: Presenter already exists; updating the password."
fi

aws cognito-idp admin-set-user-password \
  --user-pool-id "${USER_POOL_ID}" \
  --username "${EMAIL}" \
  --password "${PASSWORD}" \
  --permanent

echo "NOTE: Presenter ready. Sign in at ${WEB_URL}"

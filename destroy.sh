#!/bin/bash
# ==============================================================================
# Tear Down the Demo
# ==============================================================================
# MicroVMs are not owned by Terraform, so they are terminated first. The image
# cannot be deleted while sessions launched from it are still alive.
# ==============================================================================
set -euo pipefail
export AWS_DEFAULT_REGION="us-east-1"
cd "$(dirname "$0")"

# ------------------------------------------------------------------------------
# TERMINATE MICROVM SESSIONS
# ------------------------------------------------------------------------------
# Inventory the service rather than DynamoDB, so orphans left by a failed
# controller call are still found and terminated.
# ------------------------------------------------------------------------------
if [[ -f 01-microvms/terraform.tfstate ]]; then
  IMAGE_ARN=$(terraform -chdir=01-microvms output -raw image_arn 2>/dev/null || echo "")

  if [[ -n "${IMAGE_ARN}" ]]; then
    echo "NOTE: Terminating MicroVM sessions for ${IMAGE_ARN}..."

    VM_IDS=$(aws lambda-microvms list-microvms --image-identifier "${IMAGE_ARN}" \
      --query "items[?state!='TERMINATED'].microvmId" --output text)

    for vm_id in ${VM_IDS}; do
      echo "NOTE: Terminating ${vm_id}..."
      aws lambda-microvms terminate-microvm --microvm-identifier "${vm_id}" >/dev/null || true
    done

    # The image delete fails while any session is still TERMINATING.
    for vm_id in ${VM_IDS}; do
      for ((attempt = 1; attempt <= 60; attempt++)); do
        state=$(aws lambda-microvms get-microvm --microvm-identifier "${vm_id}" \
          --query "state" --output text 2>/dev/null || echo "TERMINATED")
        [[ "${state}" == "TERMINATED" ]] && break
        sleep 2
      done
    done
    echo "NOTE: All MicroVM sessions terminated."
  fi
fi

# ------------------------------------------------------------------------------
# DESTROY TERRAFORM RESOURCES IN REVERSE ORDER
# ------------------------------------------------------------------------------
for phase in 03-webapp 02-lambdas 01-microvms; do
  if [[ -f "${phase}/terraform.tfstate" ]]; then
    echo "NOTE: Destroying ${phase}..."
    terraform -chdir="${phase}" init -input=false
    terraform -chdir="${phase}" destroy -auto-approve
  else
    echo "NOTE: ${phase} has no local state; nothing was deployed from this checkout."
  fi
done

echo "NOTE: Infrastructure teardown complete."

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
  # Every runtime's image, not just one: a session left behind on either blocks
  # that image's deletion.
  IMAGE_ARNS=$(terraform -chdir=01-microvms output -json images 2>/dev/null \
    | jq -r '.[]?.image_arn' 2>/dev/null || echo "")

  for IMAGE_ARN in ${IMAGE_ARNS}; do
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
    echo "NOTE: All sessions for ${IMAGE_ARN##*:} terminated."
  done
fi

# ------------------------------------------------------------------------------
# RESTORE BUILD ARTIFACTS THE CONFIGURATION REFERENCES
# ------------------------------------------------------------------------------
# Terraform evaluates the whole configuration before destroying anything, and
# both phases hash dist/*.zip with filesha256/filebase64sha256. A cleaned dist
# directory therefore breaks teardown outright. Only existence matters here, so
# rebuild cheaply and skip the pip download the real controller package needs.
# ------------------------------------------------------------------------------
if [[ ! -f dist/bash-app.zip ]]; then
  echo "NOTE: Rebuilding dist/bash-app.zip so the configuration can be evaluated..."
  mkdir -p dist
  (cd 01-microvms/bash && zip -q -X -r ../../dist/bash-app.zip Dockerfile server.py worker.sh)
fi

if [[ ! -f dist/controller.zip ]]; then
  echo "NOTE: Rebuilding a placeholder dist/controller.zip for evaluation..."
  rm -rf dist/build && mkdir -p dist/build
  cp 02-lambdas/app/*.py dist/build/
  (cd dist/build && zip -q -X -r ../controller.zip .)
fi

# ------------------------------------------------------------------------------
# RECONSTRUCT MISSING VARIABLE FILES
# ------------------------------------------------------------------------------
# apply.sh writes these and they are gitignored, so a deployment applied before
# they existed -- or a checkout that lost them -- has none. Rebuild rather than
# refuse: refusing strands infrastructure whose MicroVMs this script has already
# terminated, and forces a full image rebuild purely to permit teardown.
#
# Only `region` has to be exact, because it selects the provider endpoint.
# Terraform deletes what state records, not what configuration describes, so the
# remaining values only need to evaluate. Real values are used where an earlier
# phase's outputs still hold them.
# ------------------------------------------------------------------------------
output_or() {
  terraform -chdir="$1" output -raw "$2" 2>/dev/null || printf '%s' "$3"
}

if [[ -f 01-microvms/terraform.tfstate && ! -f 01-microvms/deployment.tfvars.json ]]; then
  echo "NOTE: Reconstructing 01-microvms/deployment.tfvars.json..."

  # BaseImageVersion is an input, not an output, so recover it from the resource
  # recorded in state rather than re-querying the service.
  base_version=$(terraform -chdir=01-microvms show -json 2>/dev/null \
    | jq -r 'first(.values.root_module.resources[]?
             | select(.type == "aws_cloudcontrolapi_resource")
             | .values.desired_state | fromjson | .BaseImageVersion) // empty' \
    2>/dev/null || true)
  [[ -z "${base_version}" ]] && base_version="1"

  jq -n --arg region "${AWS_DEFAULT_REGION}" --arg version "${base_version}" \
    '{region: $region, base_image_version: $version}' \
    > 01-microvms/deployment.tfvars.json
fi

if [[ -f 02-lambdas/terraform.tfstate && ! -f 02-lambdas/deployment.tfvars.json ]]; then
  echo "NOTE: Reconstructing 02-lambdas/deployment.tfvars.json..."

  # The images map comes back whole from the phase below, which is still
  # standing at this point. If it cannot be read, a single placeholder entry is
  # enough: destroy deletes what state records, not what configuration says.
  images=$(terraform -chdir=01-microvms output -json images 2>/dev/null || true)
  if [[ -z "${images}" || "${images}" == "null" ]]; then
    images='{"bash":{"image_arn":"unused-for-destroy","image_version":"1","image_name":"unused-for-destroy"}}'
  fi

  jq -n --arg region "${AWS_DEFAULT_REGION}" \
        --arg base "$(output_or 01-microvms base_image_version 1)" \
        --argjson images "${images}" \
    '{region: $region, name: "microvms", base_image_version: $base, images: $images}' \
    > 02-lambdas/deployment.tfvars.json
fi

if [[ -f 03-webapp/terraform.tfstate && ! -f 03-webapp/deployment.tfvars.json ]]; then
  echo "NOTE: Reconstructing 03-webapp/deployment.tfvars.json..."
  jq -n --arg region "${AWS_DEFAULT_REGION}" \
        --arg bucket "$(output_or 02-lambdas web_bucket_name unused-for-destroy)" \
    '{region: $region, web_bucket_name: $bucket}' \
    > 03-webapp/deployment.tfvars.json
fi

# ------------------------------------------------------------------------------
# DESTROY TERRAFORM RESOURCES IN REVERSE ORDER
# ------------------------------------------------------------------------------
# -input=false so a genuinely unresolvable value fails loudly instead of
# stopping teardown at an interactive prompt.
# ------------------------------------------------------------------------------
for phase in 03-webapp 02-lambdas 01-microvms; do
  if [[ ! -f "${phase}/terraform.tfstate" ]]; then
    echo "NOTE: ${phase} has no local state; nothing was deployed from this checkout."
    continue
  fi

  echo "NOTE: Destroying ${phase}..."
  terraform -chdir="${phase}" init -input=false
  terraform -chdir="${phase}" destroy -auto-approve -input=false \
    -var-file=deployment.tfvars.json
done

echo "NOTE: Infrastructure teardown complete."

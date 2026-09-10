#!/bin/bash
# ==============================================================================
# File: probe-microvm-images.sh
# ==============================================================================
# Purpose:
#   Enumerates the MicroVM images available in this account: the AWS-managed
#   base images with every published version, and any images this account has
#   built on top of them.
#
#   This is the AMI-catalog equivalent. EC2 answers the same question with
#   describe-images, which takes --owners and --filters over tens of thousands
#   of AMIs; the MicroVM call accepts only maxResults and nextToken, because
#   AWS publishes the base and your Dockerfile is the difference.
#
# Notes:
#   - Changes nothing. Every call is a list or a get.
#   - Safe to run before apply.sh; an account with no images says so.
#   - Requires an AWS CLI new enough to know the service; older builds report
#     "Invalid choice: lambda-microvms".
# ==============================================================================

# ------------------------------------------------------------------------------
# GLOBAL CONFIGURATION
# ------------------------------------------------------------------------------
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
set -euo pipefail
cd "$(dirname "$0")"

section() {
  echo
  echo "================================================================================="
  echo "  $1"
  echo "================================================================================="
}

# ------------------------------------------------------------------------------
# PRE-CHECK
# ------------------------------------------------------------------------------
# The MicroVM APIs ship only in recent CLI v2 builds, and a pip-installed awscli
# on PATH will happily shadow a newer one. Fail here with the versions in hand.
# ------------------------------------------------------------------------------
if ! aws lambda-microvms help >/dev/null 2>&1; then
  echo "ERROR: This AWS CLI does not support 'lambda-microvms'."
  echo "ERROR: Found $(aws --version 2>&1) at $(command -v aws)."
  echo "ERROR: Install the latest AWS CLI v2 and make sure it precedes any"
  echo "ERROR: pip-installed awscli on your PATH."
  exit 1
fi

echo "NOTE: $(aws --version 2>&1)"
echo "NOTE: Region ${AWS_DEFAULT_REGION}"

# ------------------------------------------------------------------------------
# MANAGED BASE IMAGES
# ------------------------------------------------------------------------------
# Versions age out through DEPRECATED, EXPIRING and EXPIRED, so what is listed
# today is not necessarily what you can still build on next quarter.
# ------------------------------------------------------------------------------
section "AWS-managed base images"

BASE_IMAGES=$(aws lambda-microvms list-managed-microvm-images \
  --query 'items[].imageArn' --output text 2>/dev/null || true)

if [[ -z "${BASE_IMAGES}" ]]; then
  echo "  None returned. Check credentials and that this Region supports MicroVMs."
else
  for arn in ${BASE_IMAGES}; do
    echo "  ${arn}"
  done

  for arn in ${BASE_IMAGES}; do
    echo
    echo "  Versions of ${arn}"
    aws lambda-microvms list-managed-microvm-image-versions \
      --image-identifier "${arn}" 2>/dev/null \
      | jq -r '
          # Epoch may be seconds or milliseconds. Normalize once, before both
          # the sort and the format, so ordering cannot disagree with the dates.
          def epoch: if . > 100000000000 then . / 1000 else . end;
          .items
          | sort_by(.createdAt | epoch)
          | .[]
          | "    " + (.imageVersion | tostring)
                   + "  created " + (.createdAt | epoch | todate)
        ' || echo "    (none returned)"
  done
fi

# ------------------------------------------------------------------------------
# IMAGES BUILT BY THIS ACCOUNT
# ------------------------------------------------------------------------------
# Printed raw: this response shape is not documented in the API reference pages
# consulted so far, and a mistaken --query would hide fields worth seeing.
# ------------------------------------------------------------------------------
section "MicroVM images owned by this account"

OWN=$(aws lambda-microvms list-microvm-images 2>/dev/null || true)

if [[ -z "${OWN}" ]] || [[ "$(echo "${OWN}" | jq -r '.items | length' 2>/dev/null || echo 0)" == "0" ]]; then
  echo "  None. Run ./apply.sh to build one."
else
  echo "${OWN}"
fi

section "Probe complete"
echo "NOTE: Nothing was created, modified or deleted."
echo

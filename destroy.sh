#!/bin/bash
# ================================================================================
# Tear Down the Demo
# Terminate this image's sessions first, then destroy Terraform-managed resources.
# Any cleanup failure stops teardown so its error cannot be hidden.
# ================================================================================
source "$(dirname "$0")/scripts/common.sh"
echo "NOTE: Stopping new operations and waiting for the AWS worker..."
"$PYTHON" scripts/cloud.py quiesce
echo "NOTE: Terminating MicroVM sessions belonging to this deployment..."
"$PYTHON" scripts/lab.py cleanup
for phase in 03-webapp 02-lambdas 01-microvms; do
  if [[ -f "$phase/terraform.tfstate" ]]; then
    echo "NOTE: Destroying $phase..."
    terraform -chdir="$phase" init -input=false
    terraform -chdir="$phase" destroy -auto-approve
  else
    echo "NOTE: $phase has no local state; nothing was deployed from this checkout."
  fi
done
echo "NOTE: Infrastructure teardown complete."

#!/bin/bash
# Create a presenter without recording a password in Terraform state or shell history.
source "$(dirname "$0")/scripts/common.sh"
"$PYTHON" scripts/cloud.py user

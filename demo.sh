#!/bin/bash
# Print the AWS-hosted frontend URL. There is no local web server.
source "$(dirname "$0")/scripts/common.sh"
"$PYTHON" scripts/cloud.py url

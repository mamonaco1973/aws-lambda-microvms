# Consumed by apply.sh and passed into 02-lambdas as -var flags, so the
# controller knows which image to launch sessions from.
output "image_arn" { value = jsondecode(aws_cloudcontrolapi_resource.demo.properties).ImageArn }

# Each build produces a new version. Pinning the controller to the version that
# was just built stops it launching from a half-finished later one.
output "image_version" { value = jsondecode(aws_cloudcontrolapi_resource.demo.properties).LatestActiveImageVersion }

output "region" { value = var.region }
output "account_id" { value = data.aws_caller_identity.current.account_id }
output "name" { value = local.name }

# Where a failed image build explains itself.
output "build_log_group" { value = aws_cloudwatch_log_group.build.name }

# Keyed by runtime and consumed by apply.sh, which passes them into 02-lambdas
# so the controller knows which image to launch each runtime's sessions from.
output "images" {
  value = {
    for runtime, resource in aws_cloudcontrolapi_resource.image :
    runtime => {
      image_arn = jsondecode(resource.properties).ImageArn
      # Each build produces a new version. Pinning the controller to the version
      # just built stops it launching from a half-finished later one.
      image_version = jsondecode(resource.properties).LatestActiveImageVersion
      image_name    = local.image_names[runtime]
    }
  }
}

output "region" { value = var.region }
output "name" { value = local.name }

# Where a failed image build explains itself.
output "build_log_groups" {
  value = { for runtime, group in aws_cloudwatch_log_group.build : runtime => group.name }
}

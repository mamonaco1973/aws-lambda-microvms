output "image_arn" { value = jsondecode(aws_cloudcontrolapi_resource.demo.properties).ImageArn }
output "image_version" { value = jsondecode(aws_cloudcontrolapi_resource.demo.properties).LatestActiveImageVersion }
output "region" { value = var.region }
output "account_id" { value = data.aws_caller_identity.current.account_id }
output "build_log_group" { value = aws_cloudwatch_log_group.build.name }
output "name" { value = local.name }

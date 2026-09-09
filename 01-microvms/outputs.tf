output "image_arn" { value = awscc_lambda_microvm_image.demo.image_arn }
output "image_version" { value = awscc_lambda_microvm_image.demo.latest_active_image_version }
output "region" { value = var.region }
output "account_id" { value = data.aws_caller_identity.current.account_id }
output "build_log_group" { value = aws_cloudwatch_log_group.build.name }
output "name" { value = local.name }

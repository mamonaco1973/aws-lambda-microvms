# ================================================================================
# Lambda MicroVM Image
# Terraform owns the image and waits for the asynchronous build through AWSCC.
# Launching and suspending individual sessions is application behavior.
# ================================================================================
resource "awscc_lambda_microvm_image" "demo" {
  name                       = local.image_name
  description                = "AWS Lambda MicroVM Python session demo"
  base_image_arn             = "arn:aws:lambda:${var.region}:aws:microvm-image:al2023-1"
  base_image_version         = var.base_image_version
  build_role_arn             = aws_iam_role.build.arn
  code_artifact              = { uri = "s3://${aws_s3_bucket.artifact.id}/${aws_s3_object.app.key}" }
  cpu_configurations         = [{ architecture = "ARM_64" }]
  resources                  = [{ minimum_memory_in_mi_b = 512 }]
  additional_os_capabilities = []
  egress_network_connectors  = ["arn:aws:lambda:${var.region}:aws:network-connector:aws-network-connector:INTERNET_EGRESS"]
  environment_variables      = []
  logging                    = { cloudwatch = { log_group = aws_cloudwatch_log_group.build.name } }
  hooks = {
    port = 8081
    microvm_image_hooks = {
      ready    = "ENABLED", ready_timeout_in_seconds = 120
      validate = "ENABLED", validate_timeout_in_seconds = 60
    }
    microvm_hooks = {
      run       = "ENABLED", run_timeout_in_seconds = 10
      suspend   = "ENABLED", suspend_timeout_in_seconds = 10
      resume    = "ENABLED", resume_timeout_in_seconds = 10
      terminate = "ENABLED", terminate_timeout_in_seconds = 10
    }
  }
  tags       = [{ key = "Project", value = "aws-lambda-microvms" }]
  depends_on = [aws_iam_role_policy.build, aws_s3_bucket_public_access_block.artifact]
}

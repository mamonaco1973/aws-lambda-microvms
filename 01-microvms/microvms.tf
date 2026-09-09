# ================================================================================
# Lambda MicroVM Image
# Send the resource properties as JSON to preserve required empty arrays.
# AWSCC drops empty sets, which makes Cloud Control reject this resource model.
# Terraform manages the image directly; no CloudFormation stack is created.
# ================================================================================
resource "aws_cloudcontrolapi_resource" "demo" {
  type_name = "AWS::Lambda::MicrovmImage"
  desired_state = jsonencode({
    Name                     = local.image_name
    Description              = "AWS Lambda MicroVM Python session demo"
    BaseImageArn             = "arn:aws:lambda:${var.region}:aws:microvm-image:al2023-1"
    BaseImageVersion         = var.base_image_version
    BuildRoleArn             = aws_iam_role.build.arn
    CodeArtifact             = { Uri = "s3://${aws_s3_bucket.artifact.id}/${aws_s3_object.app.key}" }
    CpuConfigurations        = [{ Architecture = "ARM_64" }]
    Resources                = [{ MinimumMemoryInMiB = 512 }]
    AdditionalOsCapabilities = []
    EnvironmentVariables     = []
    EgressNetworkConnectors  = ["arn:aws:lambda:${var.region}:aws:network-connector:aws-network-connector:INTERNET_EGRESS"]
    Logging                  = { CloudWatch = { LogGroup = aws_cloudwatch_log_group.build.name } }
    Hooks = {
      Port = 8081
      MicrovmImageHooks = {
        Ready    = "ENABLED", ReadyTimeoutInSeconds = 120
        Validate = "ENABLED", ValidateTimeoutInSeconds = 60
      }
      MicrovmHooks = {
        Run       = "ENABLED", RunTimeoutInSeconds = 10
        Suspend   = "ENABLED", SuspendTimeoutInSeconds = 10
        Resume    = "ENABLED", ResumeTimeoutInSeconds = 10
        Terminate = "ENABLED", TerminateTimeoutInSeconds = 10
      }
    }
    Tags = [{ Key = "Project", Value = "aws-lambda-microvms" }]
  })
  depends_on = [aws_iam_role_policy.build, aws_s3_bucket_public_access_block.artifact]
}

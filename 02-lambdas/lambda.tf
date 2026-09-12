# ==============================================================================
# Controller Lambda — drives the MicroVM lifecycle for the two demo sessions
# ==============================================================================
# Launch and resume run from a pre-initialized snapshot in a few seconds, so the
# controller answers the Function URL synchronously; no queue or worker is
# involved.

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${var.name}-api"
  retention_in_days = 1
}

resource "aws_lambda_function" "api" {
  function_name = "${var.name}-api"
  role          = aws_iam_role.api.arn
  runtime       = "python3.14"
  architectures = ["arm64"]
  handler       = "handler.api"

  filename         = "${path.module}/../dist/controller.zip"
  source_code_hash = filebase64sha256("${path.module}/../dist/controller.zip")

  # The Function URL's ceiling is this number, so this IS the cell limit the
  # user experiences: 15 minutes, Lambda's maximum. It exists so a cell can
  # install packages inside the MicroVM and still answer the same request.
  timeout     = 900
  memory_size = 256

  # API Gateway's stage throttling used to be the only bound on what a stranger
  # who found the URL could cost. A Function URL has none, so this caps the
  # damage instead: past three in flight, Lambda throttles before the handler
  # runs, and each accepted request is what launches a billable MicroVM.
  reserved_concurrent_executions = 3

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.state.name

      # A JSON map keyed by runtime, so adding a runtime needs no Terraform
      # change here beyond what 01-microvms already produces.
      IMAGES = jsonencode(var.images)

      BASE_IMAGE_ARN     = local.base_image_arn
      BASE_IMAGE_VERSION = var.base_image_version
      DEMO_PASSPHRASE    = random_pet.passphrase.id

      # The guest's own identity, and the bucket the AWS CLI preset reads.
      MICROVM_ROLE_ARN = aws_iam_role.microvm.arn
      WEB_BUCKET       = aws_s3_bucket.web.id
    }
  }

  depends_on = [aws_cloudwatch_log_group.api, aws_iam_role_policy.api, aws_iam_role_policy.microvm]
}

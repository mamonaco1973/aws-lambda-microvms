# ==============================================================================
# Controller Lambda — drives the MicroVM lifecycle for the two demo sessions
# ==============================================================================
# Launch and resume run from a pre-initialized snapshot in a few seconds, so the
# controller answers API Gateway synchronously; no queue or worker is involved.

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

  # Must stay under the API Gateway 29s integration timeout.
  timeout     = 25
  memory_size = 256

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.state.name

      # A JSON map keyed by runtime, so adding a runtime needs no Terraform
      # change here beyond what 01-microvms already produces.
      IMAGES = jsonencode(var.images)

      BASE_IMAGE_ARN     = local.base_image_arn
      BASE_IMAGE_VERSION = var.base_image_version
      DEMO_PASSPHRASE    = random_pet.passphrase.id
    }
  }

  depends_on = [aws_cloudwatch_log_group.api, aws_iam_role_policy.api]
}

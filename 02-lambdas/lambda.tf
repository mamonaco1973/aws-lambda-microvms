# ==============================================================================
# Controller Lambda — drives the MicroVM lifecycle for the two demo sessions
# ==============================================================================
# Launch and resume run from a pre-initialized snapshot in a few seconds, so the
# controller answers lifecycle calls synchronously. Cells are submitted and
# polled instead, so no request here is ever long and no queue is involved.

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

  # Short, because nothing here waits for a cell any more: the controller
  # submits work and polls for it, and the MicroVM holds the result. 30s
  # covers the slowest thing this function does, which is an auto-resume of a
  # suspended MicroVM, and it puts the cell's real ceiling back where it
  # belongs -- the VM's own lifetime, not an HTTP timeout.
  timeout     = 30
  memory_size = 256

  # A second bound behind the gateway's own throttle. Each accepted request
  # can launch a billable MicroVM, and polling means many more requests than
  # before, so the cheap ones must not be able to crowd out a launch.
  reserved_concurrent_executions = 5

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

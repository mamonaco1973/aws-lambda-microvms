resource "aws_cloudwatch_log_group" "this" {
  for_each          = local.functions
  name              = "/aws/lambda/${var.name}-${each.key}"
  retention_in_days = 1
}
resource "aws_lambda_function" "this" {
  for_each         = local.functions
  function_name    = "${var.name}-${each.key}"
  role             = aws_iam_role.this[each.key].arn
  runtime          = "python3.14"
  architectures    = ["arm64"]
  handler          = "handler.${each.key}"
  filename         = "${path.module}/../dist/controller.zip"
  source_code_hash = filebase64sha256("${path.module}/../dist/controller.zip")
  timeout          = each.key == "worker" ? 240 : 25
  memory_size      = 256
  environment {
    variables = {
      TABLE_NAME    = aws_dynamodb_table.state.name
      QUEUE_URL     = aws_sqs_queue.operations.url
      IMAGE_ARN     = var.image_arn
      IMAGE_VERSION = var.image_version
    }
  }
  depends_on = [aws_cloudwatch_log_group.this, aws_iam_role_policy.this]
}

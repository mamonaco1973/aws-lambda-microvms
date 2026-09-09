resource "aws_sqs_queue" "failed" {
  name                      = "${var.name}-failed.fifo"
  fifo_queue                = true
  message_retention_seconds = 86400
  sqs_managed_sse_enabled   = true
}
resource "aws_sqs_queue" "operations" {
  name                       = "${var.name}-operations.fifo"
  fifo_queue                 = true
  visibility_timeout_seconds = 1440
  message_retention_seconds  = 3600
  sqs_managed_sse_enabled    = true
  redrive_policy             = jsonencode({ deadLetterTargetArn = aws_sqs_queue.failed.arn, maxReceiveCount = 1 })
}
resource "aws_lambda_event_source_mapping" "worker" {
  event_source_arn = aws_sqs_queue.operations.arn
  function_name    = aws_lambda_function.this["worker"].arn
  batch_size       = 1
  depends_on       = [aws_iam_role_policy.this]
}

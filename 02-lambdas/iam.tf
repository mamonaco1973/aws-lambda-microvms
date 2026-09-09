resource "aws_iam_role" "this" {
  for_each = local.functions
  name     = "${var.name}-${each.key}"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}
resource "aws_iam_role_policy" "this" {
  for_each = local.functions
  role     = aws_iam_role.this[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.this[each.key].arn}:*" },
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"], Resource = aws_dynamodb_table.state.arn },
      { Effect = "Allow", Action = each.key == "api" ? ["sqs:SendMessage"] : ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"], Resource = aws_sqs_queue.operations.arn },
      { Effect = "Allow", Action = each.key == "api" ? ["lambda:GetMicrovm"] : ["lambda:RunMicrovm", "lambda:GetMicrovm", "lambda:SuspendMicrovm", "lambda:TerminateMicrovm", "lambda:CreateMicrovmAuthToken"], Resource = var.image_arn }
    ], each.key == "worker" ? [{ Effect = "Allow", Action = ["lambda:ListMicrovms"], Resource = "*" }] : [])
  })
}

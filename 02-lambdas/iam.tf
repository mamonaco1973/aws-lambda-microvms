# ==============================================================================
# Controller Role — MicroVM lifecycle, endpoint tokens and session storage
# ==============================================================================
# ListMicrovms and PassNetworkConnector cannot be scoped to the image ARN, so
# they are granted against the service-owned connectors and "*" respectively.

resource "aws_iam_role" "api" {
  name = "${var.name}-api"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "api" {
  role = aws_iam_role.api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.api.arn}:*"
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
        Resource = aws_dynamodb_table.state.arn
      },
      {
        Effect = "Allow"
        Action = [
          "lambda:RunMicrovm",
          "lambda:GetMicrovm",
          "lambda:SuspendMicrovm",
          "lambda:TerminateMicrovm",
          "lambda:CreateMicrovmAuthToken",
        ]
        Resource = var.image_arn
      },
      {
        Effect   = "Allow"
        Action   = ["lambda:ListMicrovms"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = ["lambda:PassNetworkConnector"]
        Resource = [
          "arn:aws:lambda:${var.region}:aws:network-connector:aws-network-connector:INTERNET_EGRESS",
          "arn:aws:lambda:${var.region}:aws:network-connector:aws-network-connector:ALL_INGRESS",
        ]
      },
    ]
  })
}

# ================================================================================
# Build Role and Logs
# Runtime MicroVMs receive no execution role. Build permissions cover only this
# deployment's source artifact and logs. Build logs expire after one day.
# ================================================================================
resource "aws_cloudwatch_log_group" "build" {
  name              = local.log_name
  retention_in_days = 1
}

resource "aws_iam_role" "build" {
  name_prefix = "${local.name}-build-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
}

resource "aws_iam_role_policy" "build" {
  role = aws_iam_role.build.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = aws_s3_object.app.arn },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.build.arn}:*" }
    ]
  })
}

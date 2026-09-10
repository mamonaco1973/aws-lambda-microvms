# ==============================================================================
# Build Role and Logs — permissions Lambda assumes while building the image
# ==============================================================================
# Scoped to exactly one object and one log group. This role exists only for the
# duration of a build; MicroVMs launched from the finished image receive no
# execution role at all, so tenant code never holds AWS credentials.

# Build output is the only way to diagnose a Dockerfile or /ready hook failure.
# One-day retention keeps that available without accruing log cost.
resource "aws_cloudwatch_log_group" "build" {
  name              = local.log_name
  retention_in_days = 1
}

resource "aws_iam_role" "build" {
  # name_prefix so a rebuild under a new image name cannot collide with the
  # role still attached to the outgoing one.
  name_prefix = "${local.name}-build-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      # TagSession as well as AssumeRole: the build service tags the session it
      # assumes, and omitting it fails the build with an opaque AccessDenied.
      Action = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
}

resource "aws_iam_role_policy" "build" {
  role = aws_iam_role.build.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # This exact object, not the bucket: a build can read its own source and
      # nothing else that ever lands here.
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = aws_s3_object.app.arn },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.build.arn}:*" }
    ]
  })
}

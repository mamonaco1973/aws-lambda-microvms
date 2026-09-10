# ==============================================================================
# Build Role and Logs — permissions Lambda assumes while building the image
# ==============================================================================
# Scoped to this deployment's own artifacts and log groups. The role exists
# only for the duration of a build; MicroVMs launched from a finished image
# receive no execution role at all, so submitted code holds no AWS credentials.

# Build output is the only way to diagnose a Dockerfile or /ready hook failure.
# One-day retention keeps that available without accruing log cost.
resource "aws_cloudwatch_log_group" "build" {
  for_each          = local.runtimes
  name              = "/aws/lambda/microvms/${local.image_names[each.key]}"
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
      # These exact objects, not the bucket: a build reads the runtime sources
      # this deployment uploaded and nothing else that ever lands here.
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = [for o in aws_s3_object.app : o.arn] },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = [for g in aws_cloudwatch_log_group.build : "${g.arn}:*"] }
    ]
  })
}

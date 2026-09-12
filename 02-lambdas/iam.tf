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
        Resource = [for image in var.images : image.image_arn]
      },
      {
        Effect   = "Allow"
        Action   = ["lambda:ListMicrovms"]
        Resource = "*"
      },
      # RunMicrovm hands the execution role to the guest, and IAM treats that
      # as passing a role. Scoped to this one role: without the condition, the
      # controller could give a MicroVM any role in the account.
      {
        Effect    = "Allow"
        Action    = ["iam:PassRole"]
        Resource  = aws_iam_role.microvm.arn
        Condition = { StringEquals = { "iam:PassedToService" = "lambda.amazonaws.com" } }
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

# ==============================================================================
# MicroVM Execution Role — the guest's identity, read-only on the web bucket
# ==============================================================================
# The MicroVM equivalent of an EC2 instance profile: RunMicrovm attaches this,
# and the AWS CLI inside the guest authenticates as it with no key material
# stored anywhere in the VM.
#
# Deliberately almost powerless. Submitted code runs through eval in the
# session shell, so this role's permissions ARE the permissions of anyone who
# gets past the demo passphrase. Reading the four public objects the SPA is
# already serving to the internet anonymously adds no exposure, which is
# exactly why that bucket was chosen as the demonstration target.

resource "aws_iam_role" "microvm" {
  name = "${var.name}-microvm"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      # TagSession alongside AssumeRole, matching the build role: the service
      # tags the session it assumes, and omitting it fails with an opaque
      # AccessDenied rather than anything that names the cause.
      Action = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
}

resource "aws_iam_role_policy" "microvm" {
  role = aws_iam_role.microvm.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      # ListBucket is on the bucket ARN and GetObject on its contents -- two
      # different resource shapes for what reads as one permission.
      Action   = ["s3:ListBucket", "s3:GetObject"]
      Resource = [aws_s3_bucket.web.arn, "${aws_s3_bucket.web.arn}/*"]
    }]
  })
}

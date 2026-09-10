# ==============================================================================
# MicroVM Images — one pre-initialized snapshot per runtime
# ==============================================================================
# Two images built from the same recipe: Lambda runs each Dockerfile, starts the
# application, waits for its /ready hook, then snapshots disk AND memory. A
# launch restores a warm interpreter rather than booting and initializing one.
#
# The two differ only in their Dockerfile and server implementation. Every other
# property below is identical, which is the point: the runtime is your business,
# the platform is AWS's.
#
# Sent as JSON through Cloud Control rather than the AWSCC provider: nearly
# every property of AWS::Lambda::MicrovmImage is Required, including the empty
# arrays below, and AWSCC drops empty sets, which Cloud Control then rejects.

resource "aws_cloudcontrolapi_resource" "image" {
  for_each  = local.runtimes
  type_name = "AWS::Lambda::MicrovmImage"

  desired_state = jsonencode({
    Name        = local.image_names[each.key]
    Description = each.value

    # AWS-managed AL2023 base. There is exactly one; only its version varies.
    # BaseImageVersion is required by the schema even though the API would
    # otherwise default to the newest.
    BaseImageArn     = "arn:aws:lambda:${var.region}:aws:microvm-image:al2023-1"
    BaseImageVersion = var.base_image_version

    BuildRoleArn = aws_iam_role.build.arn
    CodeArtifact = { Uri = "s3://${aws_s3_bucket.artifact.id}/${aws_s3_object.app[each.key].key}" }

    CpuConfigurations = [{ Architecture = "ARM_64" }]

    # Smallest baseline (0.5 GB / 0.25 vCPU), bursting to 4x under load.
    Resources = [{ MinimumMemoryInMiB = 512 }]

    # Required by the schema, and both must stay present even when empty.
    AdditionalOsCapabilities = []
    EnvironmentVariables     = []

    # Internet egress so submitted code can behave like a real session. There
    # is deliberately no execution role, so the VM holds no AWS credentials.
    EgressNetworkConnectors = ["arn:aws:lambda:${var.region}:aws:network-connector:aws-network-connector:INTERNET_EGRESS"]

    Logging = { CloudWatch = { LogGroup = aws_cloudwatch_log_group.build[each.key].name } }

    Hooks = {
      # Hooks listen on 8081 while the application serves 8080. Endpoint tokens
      # are scoped to 8080 only, so application traffic can never drive a
      # lifecycle hook.
      Port = 8081

      # Ready decides when the snapshot is taken -- it does not pass until the
      # session process is up. Validate exercises the built image and lets
      # Lambda prefetch the pages a real request touches.
      MicrovmImageHooks = {
        Ready    = "ENABLED", ReadyTimeoutInSeconds = 120
        Validate = "ENABLED", ValidateTimeoutInSeconds = 60
      }

      # Run is the important one: it generates this session's nonce after
      # restore, because anything created before the snapshot is shared by
      # every clone launched from it.
      MicrovmHooks = {
        Run       = "ENABLED", RunTimeoutInSeconds = 10
        Suspend   = "ENABLED", SuspendTimeoutInSeconds = 10
        Resume    = "ENABLED", ResumeTimeoutInSeconds = 10
        Terminate = "ENABLED", TerminateTimeoutInSeconds = 10
      }
    }

    Tags = [
      { Key = "Project", Value = "aws-lambda-microvms" },
      { Key = "Runtime", Value = each.key },
    ]
  })

  # The build role must be able to read the artifact before the build starts,
  # and the artifact bucket must be locked down before anything is written.
  depends_on = [aws_iam_role_policy.build, aws_s3_bucket_public_access_block.artifact]
}

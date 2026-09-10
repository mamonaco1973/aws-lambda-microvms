# ==============================================================================
# Build Artifact — the source zip Lambda builds the MicroVM image from
# ==============================================================================
# Lambda pulls this zip and builds the ARM64 image remotely, so no local Docker
# daemon, buildx or ECR repository is needed anywhere in this project.

resource "aws_s3_bucket" "artifact" {
  # bucket_prefix, not bucket: S3 names are globally unique, and a fixed name
  # would collide the moment this demo is deployed from a second account.
  bucket_prefix = "${local.name}-"
  force_destroy = true
}

# Build source only. Nothing here is ever served to a browser, so every public
# access path stays shut.
resource "aws_s3_bucket_public_access_block" "artifact" {
  bucket                  = aws_s3_bucket.artifact.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifact" {
  bucket = aws_s3_bucket.artifact.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

# source_hash forces a re-upload when the packaged application changes, which is
# what makes a code edit actually reach the next image build.
resource "aws_s3_object" "app" {
  bucket      = aws_s3_bucket.artifact.id
  key         = "${local.image_name}.zip"
  source      = "${path.module}/../dist/app.zip"
  source_hash = filesha256("${path.module}/../dist/app.zip")
}

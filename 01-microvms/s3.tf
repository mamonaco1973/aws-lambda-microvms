# ================================================================================
# Build Artifact
# Lambda builds the ARM64 image remotely; a local Docker daemon is not required.
# ================================================================================
resource "aws_s3_bucket" "artifact" {
  bucket_prefix = "${local.name}-"
  force_destroy = true
}

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

resource "aws_s3_object" "app" {
  bucket      = aws_s3_bucket.artifact.id
  key         = "${local.image_name}.zip"
  source      = "${path.module}/../dist/app.zip"
  source_hash = filesha256("${path.module}/../dist/app.zip")
}

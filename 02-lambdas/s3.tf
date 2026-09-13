# ==============================================================================
# Web Bucket — static SPA hosting over the regional S3 HTTPS endpoint
# ==============================================================================
# No CloudFront and no custom domain: the bucket's own regional REST endpoint
# already serves HTTPS, which is all the SPA and the API's CORS rule require.
#
# Public assets contain no credentials. config.json supplies the API URL,
# Cognito domain and public SPA client ID.

resource "aws_s3_bucket" "web" {
  bucket = "${var.name}-web-${data.aws_caller_identity.current.account_id}"

  # The demo is torn down and rebuilt often; never block destroy on objects.
  force_destroy = true
}

# ACLs stay blocked; anonymous read is granted by bucket policy alone, so the
# grant is auditable in one place instead of scattered across object ACLs.
resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = false
  restrict_public_buckets = false
}

# Enumerated keys rather than a prefix wildcard, so an object added to this
# bucket later is not published to the internet by accident.
resource "aws_s3_bucket_policy" "web" {
  bucket = aws_s3_bucket.web.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Principal = "*", Action = "s3:GetObject"
      Resource = [for file in ["index.html", "callback.html", "app.js",
      "style.css", "config.json"] : "${aws_s3_bucket.web.arn}/${file}"]
    }]
  })

  # A policy granting public read is rejected while block_public_policy is on.
  depends_on = [aws_s3_bucket_public_access_block.web]
}

# ==============================================================================
# Share Bucket — staging for files handed back as download links
# ==============================================================================
# Private, and written only by the controller Lambda. The MicroVM never touches
# it: the controller already holds credentials, so staging uploads here keeps
# the guest's execution role as small as it is.
#
# Nothing here is durable. Objects are download-once-ish scratch behind a
# presigned URL, and the lifecycle rule reaps them the next day whether or not
# anyone collected them.
resource "aws_s3_bucket" "share" {
  bucket        = "${var.name}-share-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "share" {
  bucket                  = aws_s3_bucket.share.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

# One day is already far longer than the one-hour presigned URL; this exists so
# a forgotten object cannot accrue storage cost indefinitely.
resource "aws_s3_bucket_lifecycle_configuration" "share" {
  bucket = aws_s3_bucket.share.id

  rule {
    id     = "expire-staged-downloads"
    status = "Enabled"
    filter {}
    expiration { days = 1 }
  }
}

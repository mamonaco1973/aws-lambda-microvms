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

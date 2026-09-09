# Public read applies only to static assets. Session data and APIs require Cognito.
# Use the regional S3 HTTPS object endpoint, matching aws-cognito-app.
resource "aws_s3_bucket" "web" {
  bucket        = "${var.name}-web-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
}
resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = false
  restrict_public_buckets = false
}
resource "aws_s3_bucket_policy" "web" {
  bucket = aws_s3_bucket.web.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow", Principal = "*", Action = "s3:GetObject"
      Resource = [for file in ["index.html", "callback.html", "auth.js", "app.js", "style.css", "config.json"] : "${aws_s3_bucket.web.arn}/${file}"]
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.web]
}

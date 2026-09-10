# ==============================================================================
# Static Assets — the SPA, plus the generated endpoint configuration
# ==============================================================================
# config.json is written by apply.sh from the 02-lambdas outputs, so the browser
# discovers the API URL at page load instead of having it baked into committed
# source. It is gitignored for that reason. The demo passphrase is NOT in here:
# it is typed by the user, because anything shipped to the browser is public.

locals {
  # Explicit content types: S3 serves application/octet-stream otherwise, and a
  # browser will refuse to execute a script delivered that way.
  files = {
    "index.html"  = "text/html; charset=utf-8"
    "app.js"      = "text/javascript; charset=utf-8"
    "style.css"   = "text/css; charset=utf-8"
    "config.json" = "application/json"
  }
}

resource "aws_s3_object" "assets" {
  for_each = local.files
  bucket   = var.web_bucket_name
  key      = each.key
  source   = "${path.module}/${each.key}"

  # etag on content, so editing a file actually republishes it.
  etag         = filemd5("${path.module}/${each.key}")
  content_type = each.value

  # The demo is reapplied often and endpoints change with it; never let a
  # browser cache its way into pointing at a torn-down API.
  cache_control = "no-store"
}

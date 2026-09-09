locals {
  files = {
    "index.html"    = "text/html; charset=utf-8"
    "callback.html" = "text/html; charset=utf-8"
    "auth.js"       = "text/javascript; charset=utf-8"
    "app.js"        = "text/javascript; charset=utf-8"
    "style.css"     = "text/css; charset=utf-8"
  }
}
resource "aws_s3_object" "assets" {
  for_each      = local.files
  bucket        = var.web_bucket_name
  key           = each.key
  source        = "${path.module}/${each.key}"
  etag          = filemd5("${path.module}/${each.key}")
  content_type  = each.value
  cache_control = "no-store"
}
resource "aws_s3_object" "config" {
  bucket        = var.web_bucket_name
  key           = "config.json"
  content       = jsonencode(var.web_config)
  content_type  = "application/json"
  cache_control = "no-store"
}

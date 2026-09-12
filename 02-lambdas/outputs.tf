output "web_bucket_name" { value = aws_s3_bucket.web.id }
output "web_url" { value = "${local.spa_origin}/index.html" }
# Function URLs carry a trailing slash; trimmed so the SPA can concatenate
# "/api/..." without producing a double slash.
output "api_url" { value = trimsuffix(aws_lambda_function_url.this.function_url, "/") }

# Printed by validate.sh so it can be typed into the application. Marked
# sensitive so a stray `terraform output` does not splash it across a recording;
# `terraform output -raw demo_passphrase` still reads it deliberately.
output "demo_passphrase" {
  value     = random_pet.passphrase.id
  sensitive = true
}

# Written to config.json by apply.sh and read by the browser at page load. The
# passphrase is deliberately absent: config.json is world-readable.
output "web_config" {
  value = {
    apiBaseUrl = trimsuffix(aws_lambda_function_url.this.function_url, "/")
  }
}

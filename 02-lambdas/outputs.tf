output "web_bucket_name" { value = aws_s3_bucket.web.id }
output "web_url" { value = "${local.spa_origin}/index.html" }
output "api_url" { value = aws_apigatewayv2_api.this.api_endpoint }

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
    apiBaseUrl = aws_apigatewayv2_api.this.api_endpoint
  }
}

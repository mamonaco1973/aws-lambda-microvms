output "web_bucket_name" { value = aws_s3_bucket.web.id }
output "web_url" { value = "${local.spa_origin}/index.html" }
output "api_url" { value = aws_apigatewayv2_api.this.api_endpoint }

output "mcp_url" { value = "${aws_apigatewayv2_api.this.api_endpoint}/mcp" }
output "cognito_domain" { value = aws_cognito_user_pool_domain.this.domain }
output "cognito_user_pool_id" { value = aws_cognito_user_pool.this.id }

# Written to config.json by apply.sh and read by the browser at page load.
# config.json is world-readable, which is fine: a public client id and a hosted
# UI domain are both public by design, and PKCE is what makes that safe.
output "web_config" {
  value = {
    apiBaseUrl = aws_apigatewayv2_api.this.api_endpoint
    # Bare host, no scheme: callback.html builds "https://" + this.
    cognitoDomain = "${aws_cognito_user_pool_domain.this.domain}.auth.${var.region}.amazoncognito.com"
    clientId      = aws_cognito_user_pool_client.spa.id
    redirectUri   = "${local.spa_origin}/callback.html"
  }
}

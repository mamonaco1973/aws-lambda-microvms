output "web_bucket_name" { value = aws_s3_bucket.web.id }
output "web_url" { value = "${local.spa_origin}/index.html" }
output "user_pool_id" { value = aws_cognito_user_pool.this.id }
output "api_url" { value = aws_apigatewayv2_api.this.api_endpoint }

# Written to config.json by apply.sh and read by the browser at page load.
output "web_config" {
  value = {
    cognitoDomain = "${aws_cognito_user_pool_domain.this.domain}.auth.${var.region}.amazoncognito.com"
    clientId      = aws_cognito_user_pool_client.spa.id
    redirectUri   = "${local.spa_origin}/callback.html"
    logoutUri     = "${local.spa_origin}/index.html"
    apiBaseUrl    = aws_apigatewayv2_api.this.api_endpoint
    scope         = "openid email ${local.scope}"
  }
}

output "web_bucket_name" { value = aws_s3_bucket.web.id }
output "web_url" { value = "${local.spa_origin}/index.html" }
output "user_pool_id" { value = aws_cognito_user_pool.this.id }
output "api_url" { value = aws_apigatewayv2_api.this.api_endpoint }
output "state_table" { value = aws_dynamodb_table.state.name }
output "queue_url" { value = aws_sqs_queue.operations.url }
output "worker_mapping_uuid" { value = aws_lambda_event_source_mapping.worker.uuid }
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

# ==============================================================================
# HTTP API — the only public entry point to the MicroVM control plane
# ==============================================================================
# An HTTP API rather than a REST API: the JWT authorizer is native, so Cognito
# tokens are validated at the edge and an unauthenticated request never reaches
# the controller Lambda or costs an invocation.

resource "aws_apigatewayv2_api" "this" {
  name          = var.name
  protocol_type = "HTTP"

  # The SPA is served from the bucket's regional REST endpoint, so exactly one
  # origin is allowed. A wildcard would let any page drive these sessions.
  cors_configuration {
    allow_origins = [local.spa_origin]
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["authorization", "content-type"]
    max_age       = 300
  }
}

# ------------------------------------------------------------------------------
# Authorizer — validates signature, issuer, audience and expiry at the edge
# ------------------------------------------------------------------------------
resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.this.id
  name             = "cognito-jwt"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    audience = [aws_cognito_user_pool_client.spa.id]
    issuer   = "https://${aws_cognito_user_pool.this.endpoint}"
  }
}

# ------------------------------------------------------------------------------
# Integration and routes — one Lambda behind every route
# ------------------------------------------------------------------------------
resource "aws_apigatewayv2_integration" "this" {
  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"

  # The service caps this at 30s. The controller's own timeout is set below it
  # so a slow lifecycle call returns a JSON error instead of a gateway 504.
  timeout_milliseconds = 29000
}

resource "aws_apigatewayv2_route" "this" {
  for_each  = toset(["GET /api/config", "GET /api/status", "POST /api/action"])
  api_id    = aws_apigatewayv2_api.this.id
  route_key = each.key
  target    = "integrations/${aws_apigatewayv2_integration.this.id}"

  # Requiring a custom scope, not just a valid token, means a token minted for
  # some other application in the same user pool cannot drive these sessions.
  authorization_type   = "JWT"
  authorizer_id        = aws_apigatewayv2_authorizer.cognito.id
  authorization_scopes = [local.scope]
}

resource "aws_apigatewayv2_stage" "this" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true

  # Throttled hard on purpose. Each accepted request can run a MicroVM, so the
  # blast radius of a stuck browser tab is billable compute, not just 5xxs.
  default_route_settings {
    throttling_burst_limit = 10
    throttling_rate_limit  = 5
  }
}

# Scoped to this API so no other caller can invoke the controller directly.
resource "aws_lambda_permission" "api" {
  function_name = aws_lambda_function.api.function_name
  action        = "lambda:InvokeFunction"
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.this.execution_arn}/*/*"
}

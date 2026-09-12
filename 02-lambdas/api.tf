# ==============================================================================
# HTTP API â€” the only public entry point to the MicroVM control plane
# ==============================================================================
# The Lambda resolves Cognito access tokens for both API and MCP requests.
# OAuth discovery and login routes remain public. This is an implementation
# choice; gateway authorizers can also pass identity claims to integrations.

resource "aws_apigatewayv2_api" "this" {
  name          = var.name
  protocol_type = "HTTP"

  # The SPA is served from the bucket's regional REST endpoint, so exactly one
  # origin is allowed. authorization must be listed or the browser's preflight
  # rejects the Bearer token before the request is ever sent.
  cors_configuration {
    allow_origins = [local.spa_origin]
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["content-type", "authorization"]
    max_age       = 300
  }
}

resource "aws_apigatewayv2_integration" "this" {
  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"

  # The service caps this at 30s, and that cap costs nothing now: no request
  # waits for a cell. Cells are submitted and polled, so the work runs in the
  # MicroVM for as long as it needs while every request here stays short.
  # The controller timeout is 30s, above this 29s integration timeout.
  # A slow lifecycle call can therefore still produce a gateway 504.
  timeout_milliseconds = 29000
}

resource "aws_apigatewayv2_route" "this" {
  # The MCP transport and the OAuth proxy share this API with the SPA, so one
  # deployment serves both front doors and there is no second gateway to keep
  # in step.
  for_each = toset([
    "GET /api/config", "GET /api/status", "POST /api/action",
    "POST /mcp",
    "GET /.well-known/oauth-authorization-server",
    "POST /oauth/register", "GET /authorize",
    "GET /oauth/callback", "POST /oauth/token",
  ])
  api_id    = aws_apigatewayv2_api.this.id
  route_key = each.key
  target    = "integrations/${aws_apigatewayv2_integration.this.id}"
}

resource "aws_apigatewayv2_stage" "this" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true

  # Throttle request traffic, including result polling. Authentication is
  # enforced by Lambda. This is not a cap on live VMs or aggregate spending.
  default_route_settings {
    throttling_burst_limit = 10
    throttling_rate_limit  = 10
  }
}

# Scoped to this API so no other caller can invoke the controller directly.
resource "aws_lambda_permission" "api" {
  function_name = aws_lambda_function.api.function_name
  action        = "lambda:InvokeFunction"
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.this.execution_arn}/*/*"
}

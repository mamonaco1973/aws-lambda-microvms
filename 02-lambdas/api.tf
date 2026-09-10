# ==============================================================================
# HTTP API — the only public entry point to the MicroVM control plane
# ==============================================================================
# No authorizer. Requests carry a shared passphrase in a header, which the
# controller checks itself. An HTTP API cannot do API keys (that is a REST API
# feature), and a Lambda authorizer for a single string comparison would be more
# machinery than the check it performs.

resource "aws_apigatewayv2_api" "this" {
  name          = var.name
  protocol_type = "HTTP"

  # The SPA is served from the bucket's regional REST endpoint, so exactly one
  # origin is allowed. x-demo-passphrase must be listed or the browser's
  # preflight rejects it before the request is ever sent.
  cors_configuration {
    allow_origins = [local.spa_origin]
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["content-type", "x-demo-passphrase"]
    max_age       = 300
  }
}

resource "aws_apigatewayv2_integration" "this" {
  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"

  # The service caps this at 30s. The controller's own timeout sits below it so
  # a slow lifecycle call returns a JSON error rather than a gateway 504.
  timeout_milliseconds = 29000
}

resource "aws_apigatewayv2_route" "this" {
  for_each  = toset(["GET /api/config", "GET /api/status", "POST /api/action"])
  api_id    = aws_apigatewayv2_api.this.id
  route_key = each.key
  target    = "integrations/${aws_apigatewayv2_integration.this.id}"
}

resource "aws_apigatewayv2_stage" "this" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true

  # Throttled hard on purpose. Each accepted request can run a MicroVM, so with
  # no authorizer in front this is the main bound on what a stranger who finds
  # the URL can cost you.
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

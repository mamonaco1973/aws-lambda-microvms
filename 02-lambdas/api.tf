# ==============================================================================
# Function URL — the only public entry point to the MicroVM control plane
# ==============================================================================
# A Lambda Function URL rather than an API Gateway HTTP API, for one reason:
# API Gateway caps a synchronous integration at 30 seconds and will not be
# argued with. A Function URL's ceiling is the function's own timeout, so a
# cell that installs packages inside the MicroVM can run for minutes and still
# answer the original request.
#
# The cost of that is everything API Gateway was doing for free:
#
#   * AuthType NONE means AWS authenticates nothing. The handler checks the
#     shared passphrase itself and must reject before touching the MicroVM API.
#   * There is no request throttling of any kind. The stage limits this used to
#     carry (burst 10 / rate 5) are replaced by reserved concurrency on the
#     function, which bounds damage rather than preventing it.
#   * WAF cannot attach to a Function URL, and putting CloudFront in front to
#     get it back would reintroduce a ~60s origin timeout -- the exact limit
#     this change exists to escape.

resource "aws_lambda_function_url" "this" {
  function_name      = aws_lambda_function.api.function_name
  authorization_type = "NONE"

  # The SPA is served from the bucket's regional REST endpoint, so exactly one
  # origin is allowed. x-demo-passphrase must be listed or the browser's
  # preflight rejects it before the request is ever sent.
  cors {
    allow_origins = [local.spa_origin]
    allow_methods = ["GET", "POST"]
    allow_headers = ["content-type", "x-demo-passphrase"]
    max_age       = 300
  }
}

# Public invoke, scoped to the URL rather than the function: nothing can reach
# the controller by calling Invoke directly.
resource "aws_lambda_permission" "url" {
  function_name          = aws_lambda_function.api.function_name
  action                 = "lambda:InvokeFunctionUrl"
  principal              = "*"
  function_url_auth_type = "NONE"
  statement_id           = "AllowFunctionUrlPublic"
}

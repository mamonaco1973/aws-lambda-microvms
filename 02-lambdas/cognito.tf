# ==============================================================================
# Cognito — one identity for both front doors
# ==============================================================================
# The browser and Claude are the same user. The SPA signs in with PKCE and the
# MCP connector signs in through the OAuth proxy in oauth.py, but both arrive
# holding a Cognito access token for the same pool -- so the controller keys a
# session on the caller's email and one person gets one MicroVM whichever way
# they reach it.
#
# This replaced a shared passphrase in a header. The passphrase was one secret
# for everyone, could not identify anyone, and could not be revoked without
# redeploying.

resource "random_id" "suffix" {
  byte_length = 4
}

resource "aws_cognito_user_pool" "this" {
  name = "${var.name}-users-${random_id.suffix.hex}"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = false
  }

  schema {
    name                = "email"
    attribute_data_type = "String"
    required            = true
    mutable             = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  # Self-service sign-up through the hosted UI, so anyone can register and try
  # the demo without an operator creating them an account first.
  #
  # Know what this costs: every user who signs up can launch their own MicroVM,
  # which bills while it runs. The bounds are the idle policy (auto-suspend),
  # the maximum lifetime, and the controller's reserved concurrency -- not the
  # user list. Watch the account, or set allow_admin_create_user_only = true.
  admin_create_user_config {
    allow_admin_create_user_only = false
  }
}

resource "aws_cognito_user_pool_domain" "this" {
  domain       = "${var.name}-auth-${random_id.suffix.hex}"
  user_pool_id = aws_cognito_user_pool.this.id
}

# ------------------------------------------------------------------------------
# SPA client — public, PKCE, no secret
# ------------------------------------------------------------------------------
# A browser cannot keep a secret, so this client has none and relies on PKCE.
resource "aws_cognito_user_pool_client" "spa" {
  name         = "${var.name}-spa"
  user_pool_id = aws_cognito_user_pool.this.id

  generate_secret = false

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]

  callback_urls = ["${local.spa_origin}/callback.html"]
  logout_urls   = ["${local.spa_origin}/index.html"]
}

# ------------------------------------------------------------------------------
# MCP client — confidential, secret held only in the Lambda's environment
# ------------------------------------------------------------------------------
# Only our own /oauth/callback is registered. claude.ai's redirect_uri carries
# its org id and changes per install, which Cognito's exact-match allow-list
# rejects -- oauth.py brokers that, so Cognito never sees claude.ai's URL.
resource "aws_cognito_user_pool_client" "mcp" {
  name         = "${var.name}-mcp"
  user_pool_id = aws_cognito_user_pool.this.id

  generate_secret = true

  explicit_auth_flows = ["ALLOW_REFRESH_TOKEN_AUTH"]

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]

  # Claude holds the access token for a whole session and has no refresh flow,
  # so issue the Cognito maximum. Underreporting this makes the client attempt
  # an unsupported refresh and drop the session after an hour.
  access_token_validity = 24
  token_validity_units {
    access_token = "hours"
  }

  callback_urls = ["${aws_apigatewayv2_api.this.api_endpoint}/oauth/callback"]
}

# ------------------------------------------------------------------------------
# OAuth state — transient, five-minute TTL
# ------------------------------------------------------------------------------
# The flow bounces the browser Cognito -> our callback -> claude.ai across
# separate stateless invocations, so the in-flight state has to live where both
# can read it. Nothing durable is kept and TTL reaps every row.
resource "aws_dynamodb_table" "oauth_state" {
  name         = "${var.name}-oauth-${random_id.suffix.hex}"
  billing_mode = "PAY_PER_REQUEST"

  hash_key  = "pk"
  range_key = "sk"

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

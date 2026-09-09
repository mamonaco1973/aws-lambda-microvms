# Same Hosted UI + public SPA client pattern as aws-cognito-app.
resource "aws_cognito_user_pool" "this" {
  name                     = var.name
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  admin_create_user_config { allow_admin_create_user_only = false }
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
}

resource "aws_cognito_user_pool_domain" "this" {
  domain       = "${var.name}-${data.aws_caller_identity.current.account_id}"
  user_pool_id = aws_cognito_user_pool.this.id
}

resource "aws_cognito_resource_server" "demo" {
  identifier   = "microvms"
  name         = "MicroVM demo controller"
  user_pool_id = aws_cognito_user_pool.this.id
  scope {
    scope_name        = "control"
    scope_description = "Control the two presenter demo sessions"
  }
}

resource "aws_cognito_user_pool_client" "spa" {
  name                                 = "${var.name}-spa"
  user_pool_id                         = aws_cognito_user_pool.this.id
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", local.scope]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = ["${local.spa_origin}/callback.html"]
  logout_urls                          = ["${local.spa_origin}/index.html"]
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true
  access_token_validity                = 30
  id_token_validity                    = 30
  token_validity_units {
    access_token = "minutes"
    id_token     = "minutes"
  }
  depends_on = [aws_cognito_resource_server.demo]
}

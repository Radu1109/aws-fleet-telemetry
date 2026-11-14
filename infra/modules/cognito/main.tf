resource "aws_cognito_user_pool" "fleet" {
  name = "fleet-${var.environment}"

  mfa_configuration = "OFF"

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "admin_only"
      priority = 1
    }
  }
}

resource "aws_cognito_user_pool_client" "tenant_clients" {
  for_each = toset(var.tenant_client_ids)

  name         = "fleet-${var.environment}-${each.key}"
  user_pool_id = aws_cognito_user_pool.fleet.id

  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["fleet/read"]
  generate_secret                      = true
}

resource "aws_cognito_resource_server" "fleet" {
  identifier   = "fleet"
  name         = "Fleet Telemetry API"
  user_pool_id = aws_cognito_user_pool.fleet.id

  scope {
    scope_name        = "read"
    scope_description = "Read vehicle telemetry data"
  }
}

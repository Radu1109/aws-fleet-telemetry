resource "aws_apigatewayv2_api" "fleet" {
  name          = "fleet-${var.environment}"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.fleet.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_logs.arn
    format = jsonencode({
      requestId = "$context.requestId"
      ip        = "$context.identity.sourceIp"
      method    = "$context.httpMethod"
      path      = "$context.path"
      status    = "$context.status"
      latency   = "$context.responseLatency"
    })
  }
}

resource "aws_cloudwatch_log_group" "api_logs" {
  name              = "/aws/apigateway/fleet-${var.environment}"
  retention_in_days = 90
}

resource "aws_apigatewayv2_authorizer" "cognito_jwt" {
  api_id           = aws_apigatewayv2_api.fleet.id
  authorizer_type  = "JWT"
  name             = "cognito-jwt"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    # audience = the Cognito User Pool ARN (used as resource server identifier)
    audience = [var.cognito_user_pool_arn]
    # issuer must use the pool ID (not ARN) — Cognito OIDC discovery endpoint format
    issuer = "https://cognito-idp.${data.aws_region.current.name}.amazonaws.com/${var.cognito_user_pool_id}"
  }
}

data "aws_region" "current" {}

resource "aws_apigatewayv2_integration" "intake" {
  api_id                 = aws_apigatewayv2_api.fleet.id
  integration_type       = "AWS_PROXY"
  integration_uri        = var.lambda_invoke_arns["u1_intake"]
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "intake" {
  api_id    = aws_apigatewayv2_api.fleet.id
  route_key = "POST /telemetry"
  target    = "integrations/${aws_apigatewayv2_integration.intake.id}"
}

resource "aws_apigatewayv2_integration" "staff" {
  api_id                 = aws_apigatewayv2_api.fleet.id
  integration_type       = "AWS_PROXY"
  integration_uri        = var.lambda_invoke_arns["u2_staff_query"]
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "staff_vehicles" {
  api_id    = aws_apigatewayv2_api.fleet.id
  route_key = "GET /vehicles/{vehicleId}"
  target    = "integrations/${aws_apigatewayv2_integration.staff.id}"
}

resource "aws_apigatewayv2_route" "staff_alerts" {
  api_id    = aws_apigatewayv2_api.fleet.id
  route_key = "GET /vehicles/{vehicleId}/alerts"
  target    = "integrations/${aws_apigatewayv2_integration.staff.id}"
}

resource "aws_apigatewayv2_route" "staff_route_history" {
  api_id    = aws_apigatewayv2_api.fleet.id
  route_key = "GET /vehicles/{vehicleId}/route"
  target    = "integrations/${aws_apigatewayv2_integration.staff.id}"
}

resource "aws_apigatewayv2_route" "staff_reports" {
  api_id    = aws_apigatewayv2_api.fleet.id
  route_key = "GET /vehicles/{vehicleId}/reports/{utcDay}"
  target    = "integrations/${aws_apigatewayv2_integration.staff.id}"
}

resource "aws_apigatewayv2_integration" "thirdparty" {
  api_id                 = aws_apigatewayv2_api.fleet.id
  integration_type       = "AWS_PROXY"
  integration_uri        = var.lambda_invoke_arns["u2_thirdparty_query"]
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "thirdparty_status" {
  api_id             = aws_apigatewayv2_api.fleet.id
  route_key          = "GET /external/vehicles/{vehicleId}/status"
  target             = "integrations/${aws_apigatewayv2_integration.thirdparty.id}"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito_jwt.id
  authorization_type = "JWT"
}

resource "aws_lambda_permission" "api_gateway" {
  for_each = toset(["u1_intake", "u2_staff_query", "u2_thirdparty_query"])

  statement_id  = "AllowAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = var.lambda_function_names[each.key]
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.fleet.execution_arn}/*/*"
}

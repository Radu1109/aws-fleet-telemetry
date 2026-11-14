resource "aws_dynamodb_table" "client_authorization" {
  name         = "fleet-${var.environment}-client-authorization"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "clientId"

  attribute {
    name = "clientId"
    type = "S"
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }

  tags = { Unit = "u2" }
}

resource "aws_iam_role" "staff" {
  name = "fleet-${var.environment}-u2-staff"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "staff" {
  name = "u2-staff-policy"
  role = aws_iam_role.staff.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "lambda:InvokeFunction"
        Resource = [var.u1_query_function_arn, var.u3_report_worker_function_arn]
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

resource "aws_iam_role" "thirdparty" {
  name = "fleet-${var.environment}-u2-thirdparty"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "thirdparty" {
  name = "u2-thirdparty-policy"
  role = aws_iam_role.thirdparty.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "lambda:InvokeFunction"
        Resource = var.u1_query_function_arn
      },
      {
        Effect   = "Allow"
        Action   = "dynamodb:GetItem"
        Resource = aws_dynamodb_table.client_authorization.arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

resource "aws_lambda_function" "staff" {
  function_name = "fleet-${var.environment}-u2-staff-query"
  role          = aws_iam_role.staff.arn
  runtime       = "python3.11"
  handler       = "fleet_telemetry.fleet_access.staff_handler.handle"
  filename      = var.lambda_package_path
  timeout       = 15
  memory_size   = 256

  environment {
    variables = {
      U1_QUERY_FUNCTION_NAME         = var.u1_query_function_name
      U3_REPORT_WORKER_FUNCTION_NAME = var.u3_report_worker_function_name
      LOG_LEVEL                      = var.environment == "production" ? "INFO" : "DEBUG"
    }
  }

  tags = { Unit = "u2" }
}

resource "aws_lambda_function" "thirdparty" {
  function_name = "fleet-${var.environment}-u2-thirdparty-query"
  role          = aws_iam_role.thirdparty.arn
  runtime       = "python3.11"
  handler       = "fleet_telemetry.fleet_access.thirdparty_handler.handle"
  filename      = var.lambda_package_path
  timeout       = 5
  memory_size   = 256

  environment {
    variables = {
      U1_QUERY_FUNCTION_NAME     = var.u1_query_function_name
      CLIENT_AUTHORIZATION_TABLE = aws_dynamodb_table.client_authorization.name
      COGNITO_USER_POOL_ID       = var.cognito_user_pool_id
      COGNITO_REGION             = var.aws_region
      LOG_LEVEL                  = var.environment == "production" ? "INFO" : "DEBUG"
    }
  }

  tags = { Unit = "u2" }
}

resource "aws_lambda_permission" "staff_apigw" {
  statement_id  = "AllowAPIGatewayInvokeStaff"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.staff.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${var.api_execution_arn}/*/*"
}

resource "aws_lambda_permission" "thirdparty_apigw" {
  statement_id  = "AllowAPIGatewayInvokeThirdparty"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.thirdparty.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${var.api_execution_arn}/*/*"
}

locals {
  functions = {
    u1_intake             = { handler = "fleet_telemetry.telemetry_core.intake.handler", timeout = 30, memory = 256 }
    u1_outbox_relay       = { handler = "fleet_telemetry.telemetry_core.processing.relay", timeout = 60, memory = 256 }
    u1_accepted_processor = { handler = "fleet_telemetry.telemetry_core.processing.accepted", timeout = 120, memory = 512 }
    u1_receipt_processor  = { handler = "fleet_telemetry.telemetry_core.processing.receipt", timeout = 30, memory = 256 }
    u1_query              = { handler = "fleet_telemetry.telemetry_core.reads.handler", timeout = 30, memory = 256 }
    u2_staff_query        = { handler = "fleet_telemetry.fleet_access.staff_handler.handle", timeout = 15, memory = 256 }
    u2_thirdparty_query   = { handler = "fleet_telemetry.fleet_access.thirdparty_handler.handle", timeout = 5, memory = 256 }
    u3_fanout             = { handler = "fleet_telemetry.daily_reporting.job.fanout_handler", timeout = 60, memory = 256 }
    u3_report_worker      = { handler = "fleet_telemetry.daily_reporting.job.worker_handler", timeout = 120, memory = 512 }
  }

  # Per-function env vars merged with the common ENVIRONMENT variable.
  function_env_vars = {
    u1_intake = {
      OUTBOX_PROJECTIONS_TABLE = var.dynamodb_table_names["outbox_projections"]
      CANONICAL_HISTORY_TABLE  = var.dynamodb_table_names["canonical_history"]
    }
    u1_outbox_relay       = {}
    u1_accepted_processor = {}
    u1_receipt_processor  = {}
    u1_query = {
      OUTBOX_PROJECTIONS_TABLE = var.dynamodb_table_names["outbox_projections"]
      CANONICAL_HISTORY_TABLE  = var.dynamodb_table_names["canonical_history"]
    }
    u2_staff_query = {
      U1_QUERY_FUNCTION_NAME         = "fleet-${var.environment}-u1-query"
      U3_REPORT_WORKER_FUNCTION_NAME = "fleet-${var.environment}-u3-report-worker"
    }
    u2_thirdparty_query = {
      U1_QUERY_FUNCTION_NAME     = "fleet-${var.environment}-u1-query"
      CLIENT_AUTHORIZATION_TABLE = var.dynamodb_table_names["client_authorization"]
      COGNITO_USER_POOL_ID       = var.u2_cognito_user_pool_id
      COGNITO_REGION             = var.aws_region
    }
    u3_fanout        = {}
    u3_report_worker = {}
  }
}

resource "aws_lambda_function" "handlers" {
  for_each = local.functions

  function_name = "fleet-${var.environment}-${replace(each.key, "_", "-")}"
  role          = aws_iam_role.lambda_roles[each.key].arn
  handler       = each.value.handler
  runtime       = "python3.11"
  timeout       = each.value.timeout
  memory_size   = each.value.memory
  filename      = var.lambda_package_path

  environment {
    variables = merge({ ENVIRONMENT = var.environment }, local.function_env_vars[each.key])
  }

  kms_key_arn = var.kms_key_arn

  tags = { Function = each.key }
}

resource "aws_iam_role" "lambda_roles" {
  for_each = local.functions

  name = "fleet-${var.environment}-${replace(each.key, "_", "-")}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  for_each   = local.functions
  role       = aws_iam_role.lambda_roles[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "u1_intake_policy" {
  name = "u1-intake-data-access"
  role = aws_iam_role.lambda_roles["u1_intake"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query", "dynamodb:TransactWriteItems"]
        Resource = [var.dynamodb_table_arns["canonical_history"], var.dynamodb_table_arns["sequence_idempotency"], var.dynamodb_table_arns["outbox_projections"], var.dynamodb_table_arns["device_assignment"]]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = [var.kms_key_arn]
      }
    ]
  })
}

resource "aws_iam_role_policy" "u1_query_policy" {
  name = "u1-query-read-access"
  role = aws_iam_role.lambda_roles["u1_query"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:Query"]
        Resource = [var.dynamodb_table_arns["canonical_history"], var.dynamodb_table_arns["outbox_projections"]]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = [var.kms_key_arn]
      }
    ]
  })
}

resource "aws_iam_role_policy" "u2_staff_invoke_policy" {
  name = "u2-staff-invoke"
  role = aws_iam_role.lambda_roles["u2_staff_query"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = [aws_lambda_function.handlers["u1_query"].arn, aws_lambda_function.handlers["u3_report_worker"].arn]
    }]
  })
}

resource "aws_iam_role_policy" "u2_thirdparty_invoke_policy" {
  name = "u2-thirdparty-invoke"
  role = aws_iam_role.lambda_roles["u2_thirdparty_query"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = [aws_lambda_function.handlers["u1_query"].arn]
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem"]
        Resource = [var.dynamodb_table_arns["client_authorization"]]
      }
    ]
  })
}

resource "aws_iam_role_policy" "u3_fanout_policy" {
  name = "u3-fanout"
  role = aws_iam_role.lambda_roles["u3_fanout"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sqs:SendMessage"]
      Resource = [var.sqs_queue_arns["u3_report_work"]]
    }]
  })
}

resource "aws_iam_role_policy" "u3_worker_policy" {
  name = "u3-worker"
  role = aws_iam_role.lambda_roles["u3_report_worker"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = [aws_lambda_function.handlers["u1_query"].arn]
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:GetItem"]
        Resource = [var.dynamodb_table_arns["report_summaries"]]
      }
    ]
  })
}

# SQS consumption policies for event-driven processors
resource "aws_iam_role_policy" "u1_accepted_sqs_policy" {
  name = "u1-accepted-sqs-consume"
  role = aws_iam_role.lambda_roles["u1_accepted_processor"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = [var.sqs_queue_arns["accepted_reading"]]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = [var.kms_key_arn]
      }
    ]
  })
}

resource "aws_iam_role_policy" "u1_receipt_sqs_policy" {
  name = "u1-receipt-sqs-consume"
  role = aws_iam_role.lambda_roles["u1_receipt_processor"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = [var.sqs_queue_arns["receipt"]]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = [var.kms_key_arn]
      }
    ]
  })
}

resource "aws_iam_role_policy" "u3_worker_sqs_policy" {
  name = "u3-worker-sqs-consume"
  role = aws_iam_role.lambda_roles["u3_report_worker"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = [var.sqs_queue_arns["u3_report_work"]]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = [var.kms_key_arn]
      }
    ]
  })
}

# DynamoDB Streams consumption policy for outbox relay
resource "aws_iam_role_policy" "u1_outbox_stream_policy" {
  name = "u1-outbox-stream-consume"
  role = aws_iam_role.lambda_roles["u1_outbox_relay"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:GetRecords", "dynamodb:GetShardIterator", "dynamodb:DescribeStream", "dynamodb:ListStreams"]
      Resource = [var.dynamodb_outbox_stream_arn]
    }]
  })
}

# Event source mappings — wires queues/streams to their consumer Lambdas
resource "aws_lambda_event_source_mapping" "accepted_processor" {
  event_source_arn = var.sqs_queue_arns["accepted_reading"]
  function_name    = aws_lambda_function.handlers["u1_accepted_processor"].arn
  batch_size       = 10
  enabled          = true
}

resource "aws_lambda_event_source_mapping" "receipt_processor" {
  event_source_arn = var.sqs_queue_arns["receipt"]
  function_name    = aws_lambda_function.handlers["u1_receipt_processor"].arn
  batch_size       = 10
  enabled          = true
}

resource "aws_lambda_event_source_mapping" "report_worker" {
  event_source_arn = var.sqs_queue_arns["u3_report_work"]
  function_name    = aws_lambda_function.handlers["u3_report_worker"].arn
  batch_size       = 1
  enabled          = true
}

resource "aws_lambda_event_source_mapping" "outbox_relay" {
  event_source_arn  = var.dynamodb_outbox_stream_arn
  function_name     = aws_lambda_function.handlers["u1_outbox_relay"].arn
  starting_position = "TRIM_HORIZON"
  batch_size        = 10
  enabled           = true
}

resource "aws_dynamodb_table" "canonical_history" {
  name         = "fleet-${var.environment}-canonical-history"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "vehicleId"
  range_key    = "eventSortKey"

  attribute {
    name = "vehicleId"
    type = "S"
  }

  attribute {
    name = "eventSortKey"
    type = "S"
  }

  point_in_time_recovery { enabled = true }
  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }

  ttl {
    attribute_name = "expiresAt"
    enabled        = true
  }

  tags = { Unit = "u1" }
}

resource "aws_dynamodb_table" "sequence_idempotency" {
  name         = "fleet-${var.environment}-sequence-idempotency"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "deviceId"
  range_key    = "sequenceKey"

  attribute {
    name = "deviceId"
    type = "S"
  }

  attribute {
    name = "sequenceKey"
    type = "S"
  }

  point_in_time_recovery { enabled = true }
  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }

  tags = { Unit = "u1" }
}

resource "aws_dynamodb_table" "outbox_projections" {
  name         = "fleet-${var.environment}-outbox-projections"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  stream_enabled   = true
  stream_view_type = "NEW_IMAGE"

  point_in_time_recovery { enabled = true }
  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }

  tags = { Unit = "u1" }
}

resource "aws_dynamodb_table" "device_assignment" {
  name         = "fleet-${var.environment}-device-assignment"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "devicePrincipal"

  attribute {
    name = "devicePrincipal"
    type = "S"
  }

  point_in_time_recovery { enabled = true }
  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }

  tags = { Unit = "u4" }
}

resource "aws_dynamodb_table" "report_summaries" {
  name         = "fleet-${var.environment}-report-summaries"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "vehicleId"
  range_key    = "utcDay"

  attribute {
    name = "vehicleId"
    type = "S"
  }

  attribute {
    name = "utcDay"
    type = "S"
  }

  point_in_time_recovery { enabled = true }
  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }

  ttl {
    attribute_name = "expiresAt"
    enabled        = true
  }

  tags = { Unit = "u3" }
}

resource "aws_dynamodb_table" "client_authorization" {
  name         = "fleet-${var.environment}-client-authorization"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "clientId"

  attribute {
    name = "clientId"
    type = "S"
  }

  point_in_time_recovery { enabled = true }
  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }

  tags = { Unit = "u2" }
}

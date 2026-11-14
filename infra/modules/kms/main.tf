resource "aws_kms_key" "data" {
  description             = "Fleet telemetry data encryption - ${var.environment}"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  tags = {
    Environment = var.environment
    Purpose     = "data-encryption"
  }
}

resource "aws_kms_alias" "data" {
  name          = "alias/fleet-${var.environment}-data"
  target_key_id = aws_kms_key.data.key_id
}

resource "aws_kms_key" "state" {
  description             = "Terraform state encryption - ${var.environment}"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  tags = {
    Environment = var.environment
    Purpose     = "state-encryption"
  }
}

resource "aws_kms_alias" "state" {
  name          = "alias/fleet-${var.environment}-state"
  target_key_id = aws_kms_key.state.key_id
}

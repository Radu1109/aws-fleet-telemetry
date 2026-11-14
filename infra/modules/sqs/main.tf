resource "aws_sqs_queue" "accepted_reading" {
  name                       = "fleet-${var.environment}-accepted-reading"
  visibility_timeout_seconds = 180
  message_retention_seconds  = 1209600
  kms_master_key_id          = var.kms_key_arn
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.accepted_reading_dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue" "accepted_reading_dlq" {
  name                      = "fleet-${var.environment}-accepted-reading-dlq"
  message_retention_seconds = 1209600
  kms_master_key_id         = var.kms_key_arn
}

resource "aws_sqs_queue" "receipt" {
  name                       = "fleet-${var.environment}-receipt"
  visibility_timeout_seconds = 60
  message_retention_seconds  = 1209600
  kms_master_key_id          = var.kms_key_arn
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.receipt_dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue" "receipt_dlq" {
  name                      = "fleet-${var.environment}-receipt-dlq"
  message_retention_seconds = 1209600
  kms_master_key_id         = var.kms_key_arn
}

resource "aws_sqs_queue" "u3_report_work" {
  name                       = "fleet-${var.environment}-u3-report-work"
  visibility_timeout_seconds = 180
  message_retention_seconds  = 1209600
  kms_master_key_id          = var.kms_key_arn
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.u3_report_work_dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue" "u3_report_work_dlq" {
  name                      = "fleet-${var.environment}-u3-report-work-dlq"
  message_retention_seconds = 1209600
  kms_master_key_id         = var.kms_key_arn
}

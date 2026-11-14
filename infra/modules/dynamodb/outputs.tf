output "table_arns" {
  value = {
    canonical_history    = aws_dynamodb_table.canonical_history.arn
    sequence_idempotency = aws_dynamodb_table.sequence_idempotency.arn
    outbox_projections   = aws_dynamodb_table.outbox_projections.arn
    device_assignment    = aws_dynamodb_table.device_assignment.arn
    report_summaries     = aws_dynamodb_table.report_summaries.arn
    client_authorization = aws_dynamodb_table.client_authorization.arn
  }
}

output "table_names" {
  value = {
    canonical_history    = aws_dynamodb_table.canonical_history.name
    sequence_idempotency = aws_dynamodb_table.sequence_idempotency.name
    outbox_projections   = aws_dynamodb_table.outbox_projections.name
    device_assignment    = aws_dynamodb_table.device_assignment.name
    report_summaries     = aws_dynamodb_table.report_summaries.name
    client_authorization = aws_dynamodb_table.client_authorization.name
  }
}

output "outbox_stream_arn" {
  value = aws_dynamodb_table.outbox_projections.stream_arn
}

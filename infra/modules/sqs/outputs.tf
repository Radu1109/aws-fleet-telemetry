output "accepted_reading_queue_arn" {
  value = aws_sqs_queue.accepted_reading.arn
}

output "receipt_queue_arn" {
  value = aws_sqs_queue.receipt.arn
}

output "u3_report_work_queue_arn" {
  value = aws_sqs_queue.u3_report_work.arn
}

output "accepted_reading_dlq_arn" {
  value = aws_sqs_queue.accepted_reading_dlq.arn
}

output "receipt_dlq_arn" {
  value = aws_sqs_queue.receipt_dlq.arn
}

output "u3_report_work_dlq_arn" {
  value = aws_sqs_queue.u3_report_work_dlq.arn
}

output "queue_arns" {
  value = {
    accepted_reading = aws_sqs_queue.accepted_reading.arn
    receipt          = aws_sqs_queue.receipt.arn
    u3_report_work   = aws_sqs_queue.u3_report_work.arn
  }
}

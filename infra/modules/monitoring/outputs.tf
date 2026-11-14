output "dashboard_arn" {
  value = aws_cloudwatch_dashboard.fleet.dashboard_arn
}

output "alarm_arns" {
  value = [
    aws_cloudwatch_metric_alarm.intake_errors.arn,
    aws_cloudwatch_metric_alarm.backlog_age.arn,
    aws_cloudwatch_metric_alarm.dlq_depth.arn,
    aws_cloudwatch_metric_alarm.api_latency.arn,
    aws_cloudwatch_metric_alarm.backup_copy_age.arn
  ]
}

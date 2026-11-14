resource "aws_cloudwatch_dashboard" "fleet" {
  dashboard_name = "fleet-${var.environment}"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric"
        properties = {
          metrics = [
            ["AWS/Lambda", "Errors", { stat = "Sum", label = "Intake Errors" }],
            ["AWS/SQS", "ApproximateAgeOfOldestMessage", { stat = "Maximum", label = "Backlog Age" }],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", { stat = "Sum", label = "DLQ Depth" }]
          ]
          period = 300
          region = var.aws_region
          title  = "Fleet Telemetry Health"
        }
      }
    ]
  })
}

resource "aws_cloudwatch_metric_alarm" "intake_errors" {
  alarm_name          = "fleet-${var.environment}-intake-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 10
  alarm_description   = "Intake Lambda error rate exceeded"
  alarm_actions       = [var.sns_topic_arn]

  dimensions = {
    FunctionName = var.u1_intake_function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "backlog_age" {
  alarm_name          = "fleet-${var.environment}-backlog-age"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateAgeOfOldestMessage"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 3600
  alarm_description   = "SQS backlog age exceeded 1 hour"
  alarm_actions       = [var.sns_topic_arn]

  dimensions = {
    QueueName = var.accepted_reading_queue_name
  }
}

resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  alarm_name          = "fleet-${var.environment}-dlq-depth"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Messages in DLQ"
  alarm_actions       = [var.sns_topic_arn]

  dimensions = {
    QueueName = var.accepted_reading_dlq_name
  }
}

resource "aws_cloudwatch_metric_alarm" "api_latency" {
  alarm_name          = "fleet-${var.environment}-api-latency"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "IntegrationLatency"
  namespace           = "AWS/ApiGateway"
  period              = 300
  statistic           = "Average"
  threshold           = 2000
  alarm_description   = "API Gateway latency exceeded 2s"
  alarm_actions       = [var.sns_topic_arn]

  dimensions = {
    ApiId = var.api_gateway_id
  }
}

resource "aws_cloudwatch_metric_alarm" "backup_copy_age" {
  alarm_name          = "fleet-${var.environment}-backup-copy-age"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "NumberOfBackupJobsCompleted"
  namespace           = "AWS/Backup"
  period              = 86400
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "No backup copy completed in 24h"
  alarm_actions       = [var.sns_topic_arn]
  treat_missing_data  = "breaching"
}

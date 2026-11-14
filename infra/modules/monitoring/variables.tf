variable "environment" {
  description = "Environment name (staging, production)"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "sns_topic_arn" {
  description = "SNS topic ARN for alarm notifications"
  type        = string
}

variable "u1_intake_function_name" {
  description = "U1 intake Lambda function name"
  type        = string
}

variable "accepted_reading_queue_name" {
  description = "Accepted reading SQS queue name"
  type        = string
}

variable "accepted_reading_dlq_name" {
  description = "Accepted reading DLQ name"
  type        = string
}

variable "api_gateway_id" {
  description = "API Gateway ID"
  type        = string
}

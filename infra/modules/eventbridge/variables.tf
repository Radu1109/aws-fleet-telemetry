variable "environment" {
  description = "Environment name (staging, production)"
  type        = string
}

variable "u3_fanout_lambda_arn" {
  description = "ARN of the U3 fanout Lambda function"
  type        = string
}

variable "environment" {
  description = "Environment name (staging, production)"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key ARN for DynamoDB encryption"
  type        = string
}

variable "lambda_package_path" {
  description = "Path to the Lambda deployment package"
  type        = string
}

variable "u1_query_function_arn" {
  description = "ARN of the U1 query Lambda function"
  type        = string
}

variable "u1_query_function_name" {
  description = "Name of the U1 query Lambda function"
  type        = string
}

variable "u3_report_worker_function_arn" {
  description = "ARN of the U3 report worker Lambda function"
  type        = string
}

variable "u3_report_worker_function_name" {
  description = "Name of the U3 report worker Lambda function"
  type        = string
}

variable "api_execution_arn" {
  description = "API Gateway execution ARN for Lambda permissions"
  type        = string
}

variable "cognito_user_pool_id" {
  description = "Cognito User Pool ID for third-party JWT validation"
  type        = string
}

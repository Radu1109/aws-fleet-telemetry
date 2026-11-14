variable "environment" {
  description = "Environment name (staging, production)"
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key ARN for vault encryption"
  type        = string
}

variable "dynamodb_table_arns" {
  description = "Map of table name to ARN for all DynamoDB tables to back up"
  type        = map(string)
}

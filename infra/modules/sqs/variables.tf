variable "environment" {
  description = "Environment name (staging, production)"
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key ARN for queue encryption"
  type        = string
}

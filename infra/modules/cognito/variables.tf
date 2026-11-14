variable "environment" {
  description = "Environment name (staging, production)"
  type        = string
}

variable "tenant_client_ids" {
  description = "List of tenant client IDs for OAuth app clients"
  type        = list(string)
  default     = []
}

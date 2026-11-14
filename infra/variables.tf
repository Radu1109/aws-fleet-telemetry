variable "aws_region" {
  type    = string
  default = "eu-west-1"
}

variable "environment" {
  type = string
  validation {
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "Environment must be dev, staging, or production."
  }
}

variable "lambda_package_path" {
  type        = string
  description = "Path to the Lambda deployment package."
  default     = "../dist/lambda.zip"
}

variable "gitlab_project_path" {
  type        = string
  description = "GitLab project path for OIDC trust policy (e.g., group/project)."
}

variable "gitlab_default_branch" {
  type        = string
  description = "Default branch name for staging deployments."
  default     = "main"
}



variable "gitlab_project_path" {
  description = "GitLab project path (e.g., group/project)"
  type        = string
}

variable "gitlab_default_branch" {
  description = "Default branch name (e.g., main)"
  type        = string
  default     = "main"
}

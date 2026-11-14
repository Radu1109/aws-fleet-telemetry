output "staging_role_arn" {
  value = aws_iam_role.gitlab_staging.arn
}

output "production_role_arn" {
  value = aws_iam_role.gitlab_production.arn
}

output "oidc_provider_arn" {
  value = aws_iam_openid_connect_provider.gitlab.arn
}

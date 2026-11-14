output "user_pool_arn" {
  value = aws_cognito_user_pool.fleet.arn
}

output "user_pool_id" {
  value = aws_cognito_user_pool.fleet.id
}

output "client_ids" {
  value = { for k, v in aws_cognito_user_pool_client.tenant_clients : k => v.id }
}

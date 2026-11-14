output "api_gateway_url" {
  value = module.api_gateway.api_endpoint
}

output "cognito_user_pool_id" {
  value = module.cognito.user_pool_id
}

output "dynamodb_table_names" {
  value = module.dynamodb.table_names
}

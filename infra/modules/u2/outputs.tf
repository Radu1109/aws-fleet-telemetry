output "staff_lambda_arn" {
  value = aws_lambda_function.staff.arn
}

output "staff_lambda_name" {
  value = aws_lambda_function.staff.function_name
}

output "thirdparty_lambda_arn" {
  value = aws_lambda_function.thirdparty.arn
}

output "thirdparty_lambda_name" {
  value = aws_lambda_function.thirdparty.function_name
}

output "client_authorization_table_name" {
  value = aws_dynamodb_table.client_authorization.name
}

output "client_authorization_table_arn" {
  value = aws_dynamodb_table.client_authorization.arn
}

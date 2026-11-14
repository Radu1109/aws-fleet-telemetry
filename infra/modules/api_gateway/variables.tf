variable "environment" { type = string }
variable "cognito_user_pool_arn" { type = string }
variable "cognito_user_pool_id" { type = string }
variable "lambda_invoke_arns" { type = map(string) }
variable "lambda_function_names" { type = map(string) }

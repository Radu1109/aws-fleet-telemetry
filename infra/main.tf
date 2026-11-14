terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # All backend config is supplied via -backend-config flags at terraform init time.
  backend "s3" {}
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "fleet-telemetry"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

resource "aws_sns_topic" "alerts" {
  name = "fleet-${var.environment}-alerts"
}

module "kms" {
  source      = "./modules/kms"
  environment = var.environment
}

module "dynamodb" {
  source      = "./modules/dynamodb"
  environment = var.environment
  kms_key_arn = module.kms.data_key_arn
}

module "sqs" {
  source      = "./modules/sqs"
  environment = var.environment
  kms_key_arn = module.kms.data_key_arn
}

module "cognito" {
  source      = "./modules/cognito"
  environment = var.environment
}

module "lambda" {
  source                     = "./modules/lambda"
  environment                = var.environment
  aws_region                 = var.aws_region
  dynamodb_table_arns        = module.dynamodb.table_arns
  dynamodb_table_names       = module.dynamodb.table_names
  sqs_queue_arns             = module.sqs.queue_arns
  kms_key_arn                = module.kms.data_key_arn
  lambda_package_path        = var.lambda_package_path
  u2_cognito_user_pool_id    = module.cognito.user_pool_id
  dynamodb_outbox_stream_arn = module.dynamodb.outbox_stream_arn
}

module "api_gateway" {
  source                = "./modules/api_gateway"
  environment           = var.environment
  cognito_user_pool_arn = module.cognito.user_pool_arn
  cognito_user_pool_id  = module.cognito.user_pool_id
  lambda_invoke_arns    = module.lambda.invoke_arns
  lambda_function_names = module.lambda.function_names
}

module "eventbridge" {
  source               = "./modules/eventbridge"
  environment          = var.environment
  u3_fanout_lambda_arn = module.lambda.function_arns["u3_fanout"]
}

module "monitoring" {
  source                      = "./modules/monitoring"
  environment                 = var.environment
  aws_region                  = var.aws_region
  sns_topic_arn               = aws_sns_topic.alerts.arn
  u1_intake_function_name     = module.lambda.function_names["u1_intake"]
  accepted_reading_queue_name = "fleet-${var.environment}-accepted-reading"
  accepted_reading_dlq_name   = "fleet-${var.environment}-accepted-reading-dlq"
  api_gateway_id              = module.api_gateway.api_id
}

module "backup" {
  source              = "./modules/backup"
  environment         = var.environment
  dynamodb_table_arns = module.dynamodb.table_arns
  kms_key_arn         = module.kms.data_key_arn
}
# OIDC provider and roles are created once by scripts/bootstrap.ps1 and are not managed by Terraform.
# U2 resources (client_authorization table, Lambda functions, IAM policies) are
# provisioned by the dynamodb and lambda modules respectively — no separate u2 module needed.

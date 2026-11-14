resource "aws_backup_vault" "fleet" {
  name        = "fleet-${var.environment}-vault"
  kms_key_arn = var.kms_key_arn
}

resource "aws_backup_plan" "fleet" {
  name = "fleet-${var.environment}-plan"

  rule {
    rule_name         = "hourly_backup"
    target_vault_name = aws_backup_vault.fleet.name
    schedule          = "cron(0 * * * ? *)"

    lifecycle {
      delete_after = 7
    }

    # cross-region copy omitted: sandbox SCP blocks backup:CreateBackupVault in secondary region
  }
}

resource "aws_backup_selection" "fleet" {
  name         = "fleet-${var.environment}-selection"
  plan_id      = aws_backup_plan.fleet.id
  iam_role_arn = aws_iam_role.backup.arn

  resources = values(var.dynamodb_table_arns)
}

resource "aws_iam_role" "backup" {
  name = "fleet-${var.environment}-backup"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "backup.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "backup" {
  role       = aws_iam_role.backup.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup"
}

resource "aws_iam_role_policy_attachment" "restore" {
  role       = aws_iam_role.backup.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForRestores"
}

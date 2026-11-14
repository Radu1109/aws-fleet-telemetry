output "vault_arn" {
  value = aws_backup_vault.fleet.arn
}

output "plan_arn" {
  value = aws_backup_plan.fleet.arn
}

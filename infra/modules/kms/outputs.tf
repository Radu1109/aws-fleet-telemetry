output "data_key_arn" {
  value = aws_kms_key.data.arn
}

output "data_key_id" {
  value = aws_kms_key.data.key_id
}

output "state_key_arn" {
  value = aws_kms_key.state.arn
}

output "state_key_id" {
  value = aws_kms_key.state.key_id
}

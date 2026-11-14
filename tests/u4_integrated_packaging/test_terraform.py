"""
Tests for Terraform module structure and validation.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

terraform_required = pytest.mark.skipif(
    shutil.which("terraform") is None, reason="terraform not installed"
)


@terraform_required
def test_terraform_fmt():
    """Verify Terraform files are formatted correctly."""
    result = subprocess.run(
        ["terraform", "fmt", "-check", "-recursive"],
        cwd="infra",
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Terraform formatting issues: {result.stdout}"


@terraform_required
def test_terraform_validate():
    """Verify Terraform configuration is valid."""
    subprocess.run(["terraform", "init", "-backend=false"], cwd="infra", check=True)
    result = subprocess.run(
        ["terraform", "validate"],
        cwd="infra",
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Terraform validation failed: {result.stdout}"


def test_required_modules_exist():
    """Verify all required Terraform modules exist."""
    infra_path = Path("infra/modules")
    required_modules = [
        "kms", "dynamodb", "sqs", "cognito", "lambda",
        "api_gateway", "eventbridge", "monitoring", "backup", "oidc"
    ]
    
    for module in required_modules:
        module_path = infra_path / module / "main.tf"
        assert module_path.exists(), f"Module {module} main.tf not found"
        
        variables_path = infra_path / module / "variables.tf"
        assert variables_path.exists(), f"Module {module} variables.tf not found"
        
        outputs_path = infra_path / module / "outputs.tf"
        assert outputs_path.exists(), f"Module {module} outputs.tf not found"


def test_environment_tfvars_exist():
    """Verify environment-specific tfvars files exist."""
    infra_path = Path("infra/environments")
    
    staging_tfvars = infra_path / "staging.tfvars"
    assert staging_tfvars.exists(), "staging.tfvars not found"
    
    production_tfvars = infra_path / "production.tfvars"
    assert production_tfvars.exists(), "production.tfvars not found"


def test_resource_naming_convention():
    """Verify resources follow naming convention fleet-{environment}-{resource}."""
    main_tf = Path("infra/modules/dynamodb/main.tf").read_text()
    
    # Check DynamoDB table names
    assert 'name         = "fleet-${var.environment}-canonical-history"' in main_tf
    assert 'name         = "fleet-${var.environment}-sequence-idempotency"' in main_tf
    assert 'name         = "fleet-${var.environment}-outbox-projections"' in main_tf


def test_kms_encryption_enabled():
    """Verify KMS encryption is configured for data stores."""
    dynamodb_tf = Path("infra/modules/dynamodb/main.tf").read_text()
    assert "kms_key_arn" in dynamodb_tf, "DynamoDB tables missing KMS encryption"
    
    sqs_tf = Path("infra/modules/sqs/main.tf").read_text()
    assert "kms_master_key_id" in sqs_tf, "SQS queues missing KMS encryption"


def test_backup_configuration():
    """Verify AWS Backup is configured for DynamoDB tables."""
    backup_tf = Path("infra/modules/backup/main.tf")
    assert backup_tf.exists(), "Backup module not found"
    
    content = backup_tf.read_text()
    assert "aws_backup_plan" in content, "Backup plan not defined"
    assert "aws_backup_selection" in content, "Backup selection not defined"

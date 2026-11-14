"""
Tests for GitLab CI/CD pipeline configuration.
"""
from pathlib import Path

import yaml


def test_gitlab_ci_yaml_valid():
    """Verify .gitlab-ci.yml is valid YAML."""
    gitlab_ci = Path(".gitlab-ci.yml")
    assert gitlab_ci.exists(), ".gitlab-ci.yml not found"
    
    with open(gitlab_ci) as f:
        config = yaml.safe_load(f)
    
    assert config is not None, ".gitlab-ci.yml is empty or invalid"
    assert "stages" in config, "stages not defined"


def test_required_stages_defined():
    """Verify all required pipeline stages are defined."""
    with open(".gitlab-ci.yml") as f:
        config = yaml.safe_load(f)
    
    required_stages = ["lint", "test", "build", "plan", "apply"]
    assert config["stages"] == required_stages, f"Expected stages {required_stages}"


def test_mr_pipeline_jobs():
    """Verify MR pipeline has lint and test jobs."""
    with open(".gitlab-ci.yml") as f:
        config = yaml.safe_load(f)
    
    # Check terraform lint job
    assert "terraform:lint" in config
    lint_job = config["terraform:lint"]
    assert lint_job["stage"] == "lint"
    assert any(rule.get("if") == '$CI_PIPELINE_SOURCE == "merge_request_event"' 
               for rule in lint_job["rules"])
    
    # Check python test job
    assert "python:test" in config
    test_job = config["python:test"]
    assert test_job["stage"] == "test"
    assert "pytest" in " ".join(test_job["script"])


def test_staging_pipeline_jobs():
    """Verify staging pipeline has build, plan, and apply jobs."""
    with open(".gitlab-ci.yml") as f:
        config = yaml.safe_load(f)
    
    # Check build job
    assert "build:staging" in config
    build_job = config["build:staging"]
    assert build_job["environment"]["name"] == "staging"
    
    # Check plan job
    assert "terraform:plan:staging" in config
    plan_job = config["terraform:plan:staging"]
    assert "terraform plan" in " ".join(plan_job["script"])
    
    # Check apply job
    assert "terraform:apply:staging" in config
    apply_job = config["terraform:apply:staging"]
    assert "terraform apply" in " ".join(apply_job["script"])


def test_production_manual_gate():
    """Verify production apply requires manual approval."""
    with open(".gitlab-ci.yml") as f:
        config = yaml.safe_load(f)
    
    apply_job = config["terraform:apply:production"]
    assert apply_job["when"] == "manual", "Production apply must be manual"


def test_oidc_authentication():
    """Verify OIDC authentication is configured."""
    with open(".gitlab-ci.yml") as f:
        config = yaml.safe_load(f)
    
    # Check .terraform_aws_base has id_tokens (renamed from .aws_auth)
    aws_auth = config[".terraform_aws_base"]
    assert "id_tokens" in aws_auth
    assert "GITLAB_OIDC_TOKEN" in aws_auth["id_tokens"]
    
    # Check staging plan uses OIDC role
    plan_staging = config["terraform:plan:staging"]
    assert "AWS_ROLE_ARN" in plan_staging["variables"]
    assert "GitLabOIDC-staging" in plan_staging["variables"]["AWS_ROLE_ARN"]

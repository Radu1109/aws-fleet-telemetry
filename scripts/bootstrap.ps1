# One-time bootstrap: creates the S3 state bucket, OIDC provider, and IAM roles.
# Run locally with your AWS credentials once, then CI takes over.
# Usage: .\scripts\bootstrap.ps1 -GitLabProjectPath "example-org/aws-fleet-telemetry"

param(
    [Parameter(Mandatory)][string]$GitLabProjectPath,
    [string]$PrimaryRegion = "eu-west-1",
    [string]$StateRegion   = "us-east-1"
)

$accountId   = (aws sts get-caller-identity --query Account --output text)
$bucket      = "fleet-tf-state-$accountId"
$providerArn = "arn:aws:iam::${accountId}:oidc-provider/gitlab.com"

Write-Host "Account ID  : $accountId"
Write-Host "State bucket: $bucket  (region: $StateRegion)"

# --- S3 state bucket ---
aws s3api head-bucket --bucket $bucket --region $StateRegion 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "`nCreating S3 bucket..."
    if ($StateRegion -eq "us-east-1") {
        aws s3api create-bucket --bucket $bucket --region $StateRegion
    } else {
        aws s3api create-bucket --bucket $bucket --region $StateRegion `
            --create-bucket-configuration LocationConstraint=$StateRegion
    }
    aws s3api put-bucket-versioning --bucket $bucket `
        --versioning-configuration Status=Enabled
    aws s3api put-public-access-block --bucket $bucket `
        --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
    Write-Host "Bucket ready."
} else {
    Write-Host "Bucket already exists."
}

# --- GitLab OIDC provider ---
aws iam get-open-id-connect-provider --open-id-connect-provider-arn $providerArn 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "`nCreating GitLab OIDC provider..."
    aws iam create-open-id-connect-provider `
        --url https://gitlab.com `
        --client-id-list https://gitlab.com `
        --thumbprint-list b3dd7606d2b5a8b4a13771dbecc9ee1cecafa38a
    Write-Host "OIDC provider ready."
} else {
    Write-Host "OIDC provider already exists."
}

# --- Create an IAM role with JSON trust policy passed directly as inline JSON ---
function New-OIDCRole([string]$RoleName, [string]$SubCondition) {
    aws iam get-role --role-name $RoleName 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Role $RoleName already exists."
        return
    }

    # Write JSON to a BOM-free UTF-8 temp file; inline JSON is unreliable on Windows
    $json = '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Federated":"PROVIDER_ARN"},"Action":"sts:AssumeRoleWithWebIdentity","Condition":{"StringEquals":{"gitlab.com:aud":"https://gitlab.com"},"StringLike":{"gitlab.com:sub":"SUB_CONDITION"}}}]}'
    $json = $json.Replace("PROVIDER_ARN", $providerArn).Replace("SUB_CONDITION", $SubCondition)
    $tmp  = [System.IO.Path]::GetTempFileName() + ".json"
    [System.IO.File]::WriteAllText($tmp, $json, [System.Text.UTF8Encoding]::new($false))

    Write-Host "Creating role $RoleName..."
    aws iam create-role --role-name $RoleName --assume-role-policy-document "file://$tmp"
    aws iam attach-role-policy --role-name $RoleName `
        --policy-arn arn:aws:iam::aws:policy/AdministratorAccess
    Remove-Item $tmp -ErrorAction SilentlyContinue
    Write-Host "Role $RoleName ready."
}

New-OIDCRole "GitLabOIDC-staging"    "project_path:${GitLabProjectPath}:ref_type:branch:ref:main"
New-OIDCRole "GitLabOIDC-production" "project_path:${GitLabProjectPath}:ref_type:tag:ref:*"

Write-Host ""
Write-Host "=== Bootstrap complete. Set these in GitLab CI/CD Settings > Variables: ==="
Write-Host "  TF_STATE_BUCKET = $bucket"
Write-Host "  AWS_ACCOUNT_ID  = $accountId"

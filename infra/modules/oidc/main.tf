resource "aws_iam_openid_connect_provider" "gitlab" {
  url = "https://gitlab.com"

  client_id_list = [
    "https://gitlab.com"
  ]

  thumbprint_list = [
    "b3dd7606d2b5a8b4a13771dbecc9ee1cecafa38a"
  ]
}

resource "aws_iam_role" "gitlab_staging" {
  name = "GitLabOIDC-staging"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = aws_iam_openid_connect_provider.gitlab.arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "gitlab.com:aud" = "https://gitlab.com"
        }
        StringLike = {
          "gitlab.com:sub" = "project_path:${var.gitlab_project_path}:ref_type:branch:ref:${var.gitlab_default_branch}"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "gitlab_staging_admin" {
  role       = aws_iam_role.gitlab_staging.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

resource "aws_iam_role" "gitlab_production" {
  name = "GitLabOIDC-production"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = aws_iam_openid_connect_provider.gitlab.arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "gitlab.com:aud" = "https://gitlab.com"
        }
        StringLike = {
          "gitlab.com:sub" = "project_path:${var.gitlab_project_path}:ref_type:tag:ref:*"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "gitlab_production_admin" {
  role       = aws_iam_role.gitlab_production.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

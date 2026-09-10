# ==============================================================================
# Web Application Phase — uploads the SPA into the bucket built in 02-lambdas
# ==============================================================================
# Separate phase because the assets depend on outputs the controller phase
# produces (Cognito domain, client id, API URL), and because reuploading the
# frontend should never require touching Cognito or the API.

terraform {
  required_version = ">= 1.7, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

# Both supplied by apply.sh with -var; the bucket itself is owned by 02-lambdas.
variable "region" { type = string }
variable "web_bucket_name" { type = string }

provider "aws" { region = var.region }

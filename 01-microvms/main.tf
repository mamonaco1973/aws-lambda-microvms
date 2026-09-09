# ================================================================================
# Provider Configuration
# AWS manages supporting resources. AWSCC manages the image through Cloud Control.
# No CloudFormation template or stack is deployed.
# ================================================================================
terraform {
  required_version = ">= 1.7, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    awscc = {
      source  = "hashicorp/awscc"
      version = "~> 1.100"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "aws-lambda-microvms", ManagedBy = "Terraform" }
  }
}

provider "awscc" {
  region = var.region
}

data "aws_caller_identity" "current" {}

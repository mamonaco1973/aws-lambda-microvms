# ==============================================================================
# Provider Configuration
# ==============================================================================
# The MicroVM image is created through Cloud Control, which speaks to the same
# AWS::Lambda::MicrovmImage resource type as CloudFormation. No stack is created.

terraform {
  required_version = ">= 1.7, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "aws-lambda-microvms", ManagedBy = "Terraform" }
  }
}

data "aws_caller_identity" "current" {}

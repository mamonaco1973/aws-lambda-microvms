terraform {
  required_version = ">= 1.7, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

variable "region" { type = string }
variable "image_arn" { type = string }
variable "image_version" { type = string }
variable "name" { type = string }

provider "aws" { region = var.region }
data "aws_caller_identity" "current" {}

locals {
  spa_origin = "https://${aws_s3_bucket.web.bucket}.s3.${var.region}.amazonaws.com"
  scope      = "microvms/control"
  functions  = toset(["api", "worker"])
}

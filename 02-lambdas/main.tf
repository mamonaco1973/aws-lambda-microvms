terraform {
  required_version = ">= 1.7, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

variable "region" { type = string }
variable "name" { type = string }
variable "image_arn" { type = string }
variable "image_version" { type = string }

provider "aws" { region = var.region }
data "aws_caller_identity" "current" {}

locals {
  # The SPA is served from the bucket's regional REST endpoint, so that exact
  # origin must match the Cognito callback and the API's CORS allow-list.
  spa_origin = "https://${aws_s3_bucket.web.bucket}.s3.${var.region}.amazonaws.com"
  scope      = "microvms/control"
}

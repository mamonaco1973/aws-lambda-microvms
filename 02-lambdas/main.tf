terraform {
  required_version = ">= 1.7, < 2.0"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 6.0" }
    random = { source = "hashicorp/random", version = "~> 3.0" }
  }
}

variable "region" { type = string }
variable "name" { type = string }
variable "base_image_version" { type = string }

# Keyed by runtime ("python", "node"), produced by 01-microvms and passed
# through by apply.sh. The controller launches each runtime from its own image.
variable "images" {
  type = map(object({
    image_arn     = string
    image_version = string
    image_name    = string
  }))
}

provider "aws" { region = var.region }
data "aws_caller_identity" "current" {}

locals {
  # The SPA is served from the bucket's regional REST endpoint, so that exact
  # origin must match the API's CORS allow-list.
  spa_origin = "https://${aws_s3_bucket.web.bucket}.s3.${var.region}.amazonaws.com"

  base_image_arn = "arn:aws:lambda:${var.region}:aws:microvm-image:al2023-1"
}

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

# ------------------------------------------------------------------------------
# Demo passphrase — the only thing standing in front of the controller
# ------------------------------------------------------------------------------
# There is no Cognito here on purpose: a user pool, hosted UI, PKCE and a JWT
# authorizer are five moving parts that teach nothing about MicroVMs. But this
# API launches billable VMs and executes submitted code, so it cannot be open.
#
# random_pet rather than random_password because this gets typed on camera and
# read aloud. It lives in Terraform state, is printed by validate.sh, and is
# deliberately NOT written into config.json -- anything the browser is handed
# before the user types it would be public.
resource "random_pet" "passphrase" {
  length    = 3
  separator = "-"
}

locals {
  # The SPA is served from the bucket's regional REST endpoint, so that exact
  # origin must match the API's CORS allow-list.
  spa_origin = "https://${aws_s3_bucket.web.bucket}.s3.${var.region}.amazonaws.com"

  base_image_arn = "arn:aws:lambda:${var.region}:aws:microvm-image:al2023-1"
}

terraform {
  required_version = ">= 1.7, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}
variable "region" { type = string }
variable "web_bucket_name" { type = string }
variable "web_config" { type = map(string) }
provider "aws" { region = var.region }

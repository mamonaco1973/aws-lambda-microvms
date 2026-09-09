# Each separate clone gets unique resource names from its own Terraform state.
resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
}

locals {
  name       = "microvms-${random_string.suffix.result}"
  image_name = "${local.name}-${substr(filesha256("${path.module}/../dist/app.zip"), 0, 10)}"
  log_name   = "/aws/lambda/microvms/${local.image_name}"
}

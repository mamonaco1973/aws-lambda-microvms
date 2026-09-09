locals {
  name = "microvms"
  # Source changes produce a new image name, so an old snapshot built from
  # different code can never be mistaken for the current one.
  image_name = "${local.name}-${substr(filesha256("${path.module}/../dist/app.zip"), 0, 10)}"
  log_name   = "/aws/lambda/microvms/${local.image_name}"
}

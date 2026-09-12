locals {
  name = "microvms"

  # One runtime. The map is kept rather than flattened because every resource
  # below is derived from it, so adding a runtime is still one line plus a
  # directory -- and because each image carries a one-week minimum storage
  # charge, so building only what the demo uses is the cheaper default.
  runtimes = {
    bash = "Bash persistent shell"
  }

  # Source changes produce a new image name, so an old snapshot built from
  # different code can never be mistaken for the current one.
  image_names = {
    for runtime, _ in local.runtimes :
    runtime => "${local.name}-${runtime}-${substr(filesha256("${path.module}/../dist/${runtime}-app.zip"), 0, 10)}"
  }
}

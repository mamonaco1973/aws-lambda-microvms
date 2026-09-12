locals {
  name = "microvms"

  # One Bash runtime. More runtimes require packaging, presets and validation
  # alongside their map entries and source directories.
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

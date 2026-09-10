locals {
  name = "microvms"

  # The runtimes this demo builds. Everything below is derived per runtime, so
  # adding another (Ruby, R, Julia) means adding one line here plus a directory
  # containing a Dockerfile and a server that speaks the hook contract --
  # nothing else in the project needs to know.
  runtimes = {
    python = "Python 3 persistent interpreter"
    node   = "Node.js persistent interpreter"
    bash   = "Bash persistent shell"
  }

  # Source changes produce a new image name, so an old snapshot built from
  # different code can never be mistaken for the current one.
  image_names = {
    for runtime, _ in local.runtimes :
    runtime => "${local.name}-${runtime}-${substr(filesha256("${path.module}/../dist/${runtime}-app.zip"), 0, 10)}"
  }
}

mock_provider "aws" {
  mock_resource "aws_cloudcontrolapi_resource" {
    defaults = {
      properties = "{\"ImageArn\":\"arn:aws:lambda:us-east-1:123456789012:microvm-image:test\",\"LatestActiveImageVersion\":\"1\"}"
    }
  }
}
mock_provider "random" {}

variables {
  base_image_version = "test-version"
}

run "cost_and_hook_boundaries" {
  command = plan

  assert {
    condition     = jsondecode(aws_cloudcontrolapi_resource.demo.desired_state).Resources[0].MinimumMemoryInMiB == 512
    error_message = "The lab must retain the smallest baseline."
  }
  assert {
    condition     = jsondecode(aws_cloudcontrolapi_resource.demo.desired_state).Hooks.Port == 8081
    error_message = "Lifecycle hooks must use a different port from user code."
  }
  assert {
    condition = (
      length(jsondecode(aws_cloudcontrolapi_resource.demo.desired_state).AdditionalOsCapabilities) == 0 &&
      length(jsondecode(aws_cloudcontrolapi_resource.demo.desired_state).EnvironmentVariables) == 0
    )
    error_message = "Cloud Control requires both empty arrays to remain present in the JSON payload."
  }
  assert {
    condition     = jsondecode(aws_cloudcontrolapi_resource.demo.desired_state).CpuConfigurations[0].Architecture == "ARM_64"
    error_message = "The image resource schema requires the ARM_64 architecture enum."
  }
  assert {
    condition     = aws_cloudwatch_log_group.build.retention_in_days == 1
    error_message = "Build logs must expire quickly."
  }
}

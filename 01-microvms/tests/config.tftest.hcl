mock_provider "aws" {}
mock_provider "awscc" {}
mock_provider "random" {}

variables {
  base_image_version = "test-version"
}

run "cost_and_hook_boundaries" {
  command = plan

  assert {
    condition     = one(awscc_lambda_microvm_image.demo.resources).minimum_memory_in_mi_b == 512
    error_message = "The lab must retain the smallest baseline."
  }
  assert {
    condition     = awscc_lambda_microvm_image.demo.hooks.port == 8081
    error_message = "Lifecycle hooks must use a different port from user code."
  }
  assert {
    condition     = aws_cloudwatch_log_group.build.retention_in_days == 1
    error_message = "Build logs must expire quickly."
  }
}

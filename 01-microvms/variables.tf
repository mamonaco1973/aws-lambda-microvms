variable "region" {
  type    = string
  default = "us-east-1"

  # Lambda MicroVMs are not available everywhere. Failing in plan with a clear
  # message beats an opaque Cloud Control error minutes into an image build.
  validation {
    condition     = contains(["us-east-1", "us-east-2", "us-west-2", "eu-west-1", "ap-northeast-1"], var.region)
    error_message = "Use a documented MicroVM launch Region."
  }
}

variable "base_image_version" {
  type = string

  # Required by AWS::Lambda::MicrovmImage, and managed base image versions age
  # out through DEPRECATED/EXPIRING, so apply.sh resolves the newest at deploy
  # time instead of pinning a number that quietly rots in source.
  description = "Managed base image version, resolved by apply.sh."
}

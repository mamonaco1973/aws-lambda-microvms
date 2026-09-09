variable "region" {
  type    = string
  default = "us-east-1"
  validation {
    condition     = contains(["us-east-1", "us-east-2", "us-west-2", "eu-west-1", "ap-northeast-1"], var.region)
    error_message = "Use a documented MicroVM launch Region."
  }
}

variable "base_image_version" {
  type        = string
  description = "AVAILABLE managed base image version, resolved by apply.sh."
}

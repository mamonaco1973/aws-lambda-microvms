# ==============================================================================
# S3 Files - the same managed NFS pattern as aws-s3-files, without AD or a gateway
# ==============================================================================
# One AZ is intentional for this short-lived demo. NAT preserves the existing
# package-install presets; the S3 gateway endpoint avoids routing S3 through NAT.
data "aws_availability_zones" "storage" { state = "available" }

resource "aws_vpc" "storage" {
  cidr_block           = "10.87.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${var.name}-storage" }
}
resource "aws_subnet" "storage_public" {
  vpc_id            = aws_vpc.storage.id
  cidr_block        = "10.87.0.0/24"
  availability_zone = data.aws_availability_zones.storage.names[0]
}
resource "aws_subnet" "storage_private" {
  vpc_id            = aws_vpc.storage.id
  cidr_block        = "10.87.1.0/24"
  availability_zone = data.aws_availability_zones.storage.names[0]
}
resource "aws_internet_gateway" "storage" { vpc_id = aws_vpc.storage.id }
resource "aws_eip" "storage" { domain = "vpc" }
resource "aws_nat_gateway" "storage" {
  allocation_id = aws_eip.storage.id
  subnet_id     = aws_subnet.storage_public.id
  depends_on    = [aws_internet_gateway.storage]
}
resource "aws_route_table" "storage_public" {
  vpc_id = aws_vpc.storage.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.storage.id
  }
}
resource "aws_route_table" "storage_private" {
  vpc_id = aws_vpc.storage.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.storage.id
  }
}
resource "aws_route_table_association" "storage_public" {
  subnet_id      = aws_subnet.storage_public.id
  route_table_id = aws_route_table.storage_public.id
}
resource "aws_route_table_association" "storage_private" {
  subnet_id      = aws_subnet.storage_private.id
  route_table_id = aws_route_table.storage_private.id
}
resource "aws_vpc_endpoint" "storage_s3" {
  vpc_id            = aws_vpc.storage.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.storage_private.id]
}
resource "aws_security_group" "storage_client" {
  name_prefix = "${var.name}-storage-client-"
  vpc_id      = aws_vpc.storage.id
  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
}
resource "aws_security_group" "storage_nfs" {
  name_prefix = "${var.name}-nfs-"
  vpc_id      = aws_vpc.storage.id
  ingress {
    protocol        = "tcp"
    from_port       = 2049
    to_port         = 2049
    security_groups = [aws_security_group.storage_client.id]
  }
}

resource "aws_iam_role" "connector" {
  name = "${var.name}-network-connector"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "network-connectors.lambda.amazonaws.com" }, Action = ["sts:AssumeRole", "sts:TagSession"] }]
  })
}
resource "aws_iam_role_policy" "connector" {
  role = aws_iam_role.connector.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ec2:CreateNetworkInterface"], Resource = [
        "arn:aws:ec2:${var.region}:${data.aws_caller_identity.current.account_id}:network-interface/*",
        aws_subnet.storage_private.arn, aws_security_group.storage_client.arn
      ] },
      { Effect = "Allow", Action = ["ec2:CreateTags"], Resource = "arn:aws:ec2:${var.region}:${data.aws_caller_identity.current.account_id}:network-interface/*",
      Condition = { StringEquals = { "ec2:ManagedResourceOperator" = "network-connectors.lambda.amazonaws.com" } } },
      { Effect = "Allow", Action = ["ec2:DescribeNetworkInterfaces", "ec2:DescribeSubnets", "ec2:DescribeSecurityGroups", "ec2:DescribeVpcs"], Resource = "*" }
    ]
  })
}
# Terraform owns the connector through Cloud Control, just like the image.
# This does not create a CloudFormation stack.
resource "aws_cloudcontrolapi_resource" "storage_connector" {
  type_name = "AWS::Lambda::NetworkConnector"
  desired_state = jsonencode({
    Name         = "${var.name}-storage"
    OperatorRole = aws_iam_role.connector.arn
    Configuration = { VpcEgressConfiguration = {
      SubnetIds                      = [aws_subnet.storage_private.id]
      SecurityGroupIds               = [aws_security_group.storage_client.id]
      NetworkProtocol                = "IPv4"
      AssociatedComputeResourceTypes = ["MicroVm"]
    } }
    Tags = [{ Key = "Project", Value = "aws-lambda-microvms" }]
  })
  depends_on = [aws_iam_role_policy.connector, aws_route_table_association.storage_private, aws_route_table_association.storage_public]
}

resource "aws_s3_bucket" "storage" {
  bucket_prefix = "${var.name}-nfs-"
  force_destroy = true
}
resource "aws_s3_bucket_versioning" "storage" {
  bucket = aws_s3_bucket.storage.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_public_access_block" "storage" {
  bucket                  = aws_s3_bucket.storage.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_iam_role" "storage_service" {
  name = "${var.name}-s3files"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "elasticfilesystem.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}
resource "aws_iam_role_policy" "storage_service" {
  role = aws_iam_role.storage_service.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:ListBucket", "s3:GetBucketLocation", "s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:DeleteObject"], Resource = [aws_s3_bucket.storage.arn, "${aws_s3_bucket.storage.arn}/*"] },
      { Effect = "Allow", Action = ["events:PutRule", "events:PutTargets", "events:DeleteRule", "events:RemoveTargets", "events:DescribeRule", "events:ListTargetsByRule"], Resource = "*" }
    ]
  })
}
resource "aws_s3files_file_system" "storage" {
  bucket                = aws_s3_bucket.storage.arn
  role_arn              = aws_iam_role.storage_service.arn
  accept_bucket_warning = true
  depends_on            = [aws_s3_bucket_versioning.storage, aws_s3_bucket_public_access_block.storage, aws_iam_role_policy.storage_service]
}
resource "aws_s3files_mount_target" "storage" {
  file_system_id  = aws_s3files_file_system.storage.id
  subnet_id       = aws_subnet.storage_private.id
  security_groups = [aws_security_group.storage_nfs.id]
}
resource "aws_iam_role_policy" "storage_client" {
  role = aws_iam_role.microvm.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3files:ClientMount", "s3files:ClientWrite", "s3files:ClientRootAccess"]
      Resource = aws_s3files_file_system.storage.arn
    }]
  })
}
locals {
  storage = {
    file_system_id     = aws_s3files_file_system.storage.id
    mount_target_ip    = aws_s3files_mount_target.storage.ipv4_address
    bucket             = aws_s3_bucket.storage.id
    region             = var.region
    connector_arn      = jsondecode(aws_cloudcontrolapi_resource.storage_connector.properties).Arn
    execution_role_arn = aws_iam_role.microvm.arn
  }
}
output "storage" { value = local.storage }
output "storage_console_url" { value = "https://s3.console.aws.amazon.com/s3/buckets/${aws_s3_bucket.storage.id}?region=${var.region}&tab=objects" }

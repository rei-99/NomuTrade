# STP platform — AWS single-VM reference deployment (D-06).
# One VM per environment running the docker-compose stack, optional managed
# PostgreSQL, and an S3 bucket for report files. Cloud-portable per C-03:
# only the provider/resources change for Azure — see README.md.

locals {
  name = "stp-${var.environment}"
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# -------------------------------------------------------------------------
# Network: VPC with two public subnets (two AZs so the optional RDS subnet
# group is valid), an internet gateway and a default route.
# -------------------------------------------------------------------------

resource "aws_vpc" "this" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = local.name }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = { Name = local.name }
}

resource "aws_subnet" "public" {
  count = 2

  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(aws_vpc.this.cidr_block, 8, count.index + 1)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "${local.name}-public-${count.index + 1}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table_association" "public" {
  count = 2

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# -------------------------------------------------------------------------
# Security groups
# -------------------------------------------------------------------------

resource "aws_security_group" "app" {
  name        = "${local.name}-app"
  description = "STP app VM - web and SSH from the allowed CIDR only"
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "HTTP (reserved for TLS termination / redirect)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = [var.allowed_cidr]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.allowed_cidr]
  }

  ingress {
    description = "App UI - compose 'web' service maps host 8080 to nginx 80"
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = [var.allowed_cidr]
  }

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.allowed_cidr]
  }

  egress {
    description = "All egress (package installs, git clone, image pulls)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-app" }
}

resource "aws_security_group" "rds" {
  count = var.enable_rds ? 1 : 0

  name        = "${local.name}-rds"
  description = "PostgreSQL reachable from the app VM only"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "PostgreSQL from app VM"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  tags = { Name = "${local.name}-rds" }
}

# -------------------------------------------------------------------------
# Optional managed PostgreSQL (D-06). When enable_rds = false the compose
# 'db' container on the VM is the database instead.
# -------------------------------------------------------------------------

resource "aws_db_subnet_group" "this" {
  count = var.enable_rds ? 1 : 0

  name       = "${local.name}-db"
  subnet_ids = aws_subnet.public[*].id

  tags = { Name = "${local.name}-db" }
}

resource "aws_db_instance" "this" {
  count = var.enable_rds ? 1 : 0

  identifier     = "${local.name}-postgres"
  engine         = "postgres"
  engine_version = "15"
  instance_class = "db.t3.micro"

  allocated_storage = 20
  storage_type      = "gp3"
  storage_encrypted = true

  db_name  = "stp"
  username = "stpadmin"
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.this[0].name
  vpc_security_group_ids = [aws_security_group.rds[0].id]
  publicly_accessible    = false

  backup_retention_period = 1
  skip_final_snapshot     = true # MVP/training; enable snapshots for real use
  deletion_protection     = false

  tags = { Name = "${local.name}-postgres" }
}

# -------------------------------------------------------------------------
# Object storage for generated report files (SRS 6.3): versioned + encrypted.
# -------------------------------------------------------------------------

resource "aws_s3_bucket" "reports" {
  bucket = "${local.name}-reports-${data.aws_caller_identity.current.account_id}"

  tags = { Name = "${local.name}-reports" }
}

resource "aws_s3_bucket_versioning" "reports" {
  bucket = aws_s3_bucket.reports.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "reports" {
  bucket = aws_s3_bucket.reports.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# -------------------------------------------------------------------------
# Single application VM (D-06): user_data installs Docker + the compose
# plugin, clones the repo and starts the stack.
# -------------------------------------------------------------------------

resource "aws_instance" "app" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public[0].id
  key_name               = var.ssh_key_name
  vpc_security_group_ids = [aws_security_group.app.id]

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
    encrypted   = true
  }

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    repo_url     = var.repo_url
    db_password  = var.db_password
    rds_endpoint = var.enable_rds ? aws_db_instance.this[0].endpoint : ""
  })

  tags = { Name = "${local.name}-app" }
}

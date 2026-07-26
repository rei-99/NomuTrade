variable "aws_region" {
  description = "AWS region for all resources. ap-northeast-1 (Tokyo) matches the JPY-equities demo dataset."
  type        = string
  default     = "ap-northeast-1"
}

variable "environment" {
  description = "Environment name (dev, demo, ...). Used as a name/tag prefix so workspaces stay portable (C-03)."
  type        = string
  default     = "dev"
}

variable "instance_type" {
  description = "EC2 instance type for the single application VM (D-06). t3.medium comfortably runs the compose stack."
  type        = string
  default     = "t3.medium"
}

variable "ssh_key_name" {
  description = "Optional EC2 key-pair name for SSH access. Leave null to provision without SSH keys."
  type        = string
  default     = null
}

variable "allowed_cidr" {
  description = "CIDR allowed to reach the VM on 22/80/443/8080 (e.g. your office/VPN egress, \"203.0.113.10/32\"). Required — set consciously, never commit a real address."
  type        = string
}

variable "db_password" {
  description = "Password for PostgreSQL — the compose db container, and the RDS master user when enable_rds = true. Pass via TF_VAR_db_password or -var; never commit."
  type        = string
  sensitive   = true
}

variable "repo_url" {
  description = "Git URL of this repository, cloned by the VM user_data and started with docker compose."
  type        = string
}

variable "enable_rds" {
  description = "When true, provision a managed PostgreSQL (db.t3.micro) instead of relying on the compose db container (D-06)."
  type        = bool
  default     = false
}

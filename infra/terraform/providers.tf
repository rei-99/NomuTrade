provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "stp-trading-platform"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

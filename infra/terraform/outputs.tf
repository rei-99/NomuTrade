output "instance_public_ip" {
  description = "Public IP of the application VM."
  value       = aws_instance.app.public_ip
}

output "app_url" {
  description = "URL of the web UI (compose 'web' service maps host 8080 to nginx 80)."
  value       = "http://${aws_instance.app.public_ip}:8080"
}

output "rds_endpoint" {
  description = "Managed PostgreSQL endpoint, or null when enable_rds = false (compose db container is used)."
  value       = var.enable_rds ? aws_db_instance.this[0].endpoint : null
}

output "report_bucket" {
  description = "S3 bucket for generated report files."
  value       = aws_s3_bucket.reports.bucket
}

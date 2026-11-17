variable "aws_region" {
  description = "AWS region for deploying backup infrastructure."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment name (e.g., dev, staging, prod)."
  type        = string
  default     = "prod"
}

variable "bucket_prefix" {
  description = "Prefix for the S3 backup bucket name."
  type        = string
  default     = "aws-backup-recovery"
}

variable "retention_days_standard_ia" {
  description = "Days before transitioning backups to STANDARD_IA storage class."
  type        = number
  default     = 30
}

variable "retention_days_glacier" {
  description = "Days before transitioning backups to GLACIER storage class."
  type        = number
  default     = 90
}

variable "retention_days_expiration" {
  description = "Days before permanently deleting expired backup archives."
  type        = number
  default     = 365
}

variable "cloudwatch_log_retention_days" {
  description = "Days to retain structured logs in CloudWatch Logs."
  type        = number
  default     = 30
}

variable "alert_email" {
  description = "Email address to receive backup failure notifications via Amazon SNS (leave empty to skip subscription)."
  type        = string
  default     = ""
}

variable "backup_schedule_expression" {
  description = "EventBridge cron schedule expression for automated backups (default: daily at 02:00 UTC)."
  type        = string
  default     = "cron(0 2 * * ? *)"
}

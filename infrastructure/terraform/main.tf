# ==============================================================================
# AWS Automated Backup & Recovery System - Main Terraform Configuration
# Provisions S3 Bucket (SSE, Lifecycle, Block Public Access), IAM Role & Policy,
# CloudWatch Logs & Alarms, SNS Topic, and EventBridge Scheduling.
# ==============================================================================

resource "random_id" "bucket_suffix" {
  byte_length = 4
}

# ------------------------------------------------------------------------------
# 1. Amazon S3 Backup Bucket
# ------------------------------------------------------------------------------
resource "aws_s3_bucket" "backup_bucket" {
  bucket        = "${var.bucket_prefix}-${var.environment}-${random_id.bucket_suffix.hex}"
  force_destroy = false

  tags = {
    Name        = "Backup-Storage-Bucket"
    Purpose     = "Automated EC2 Backups"
    Environment = var.environment
  }
}

# Enforce S3 Block Public Access
resource "aws_s3_bucket_public_access_block" "backup_bucket_access" {
  bucket = aws_s3_bucket.backup_bucket.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Enable S3 Bucket Versioning for data resilience
resource "aws_s3_bucket_versioning" "backup_bucket_versioning" {
  bucket = aws_s3_bucket.backup_bucket.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Enforce Server-Side Encryption (SSE-S3 AES256)
resource "aws_s3_bucket_server_side_encryption_configuration" "backup_bucket_encryption" {
  bucket = aws_s3_bucket.backup_bucket.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# S3 Lifecycle Policy: Transition to Standard-IA, Glacier, and eventual expiration
resource "aws_s3_bucket_lifecycle_configuration" "backup_lifecycle" {
  bucket = aws_s3_bucket.backup_bucket.id

  rule {
    id     = "BackupRetentionLifecycleRule"
    status = "Enabled"

    filter {
      prefix = "backups/"
    }

    # Transition to Standard-IA after configured days (e.g., 30 days)
    transition {
      days          = var.retention_days_standard_ia
      storage_class = "STANDARD_IA"
    }

    # Transition to Glacier Flexible Retrieval after configured days (e.g., 90 days)
    transition {
      days          = var.retention_days_glacier
      storage_class = "GLACIER"
    }

    # Permanently expire backups after configured days (e.g., 365 days)
    expiration {
      days = var.retention_days_expiration
    }

    # Expire noncurrent versions after 30 days
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

# ------------------------------------------------------------------------------
# 2. Amazon SNS Alerts Topic
# ------------------------------------------------------------------------------
resource "aws_sns_topic" "backup_alerts" {
  name = "ec2-backup-alerts-${var.environment}"

  tags = {
    Name = "EC2-Backup-Alerts"
  }
}

resource "aws_sns_topic_subscription" "email_alert" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.backup_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ------------------------------------------------------------------------------
# 3. Amazon CloudWatch Logs & Alarms
# ------------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "backup_logs" {
  name              = "/aws/ec2/backup-recovery"
  retention_in_days = var.cloudwatch_log_retention_days

  tags = {
    Name = "EC2-Backup-LogGroup"
  }
}

resource "aws_cloudwatch_metric_alarm" "backup_failure_alarm" {
  alarm_name          = "EC2BackupFailureAlarm-${var.environment}"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "BackupFailure"
  namespace           = "AWSBackupRecovery/EC2"
  period              = 300
  statistic           = "Sum"
  threshold           = 1
  alarm_description   = "Triggers when an automated EC2 backup job fails."
  alarm_actions       = [aws_sns_topic.backup_alerts.arn]
  ok_actions          = [aws_sns_topic.backup_alerts.arn]

  dimensions = {
    Service = "EC2Backup"
  }

  tags = {
    Name = "EC2-Backup-Failure-Alarm"
  }
}

# ------------------------------------------------------------------------------
# 4. IAM Role and Least-Privilege Policy for EC2
# ------------------------------------------------------------------------------
resource "aws_iam_role" "ec2_backup_role" {
  name = "ec2-automated-backup-role-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EC2AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "ec2.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name = "EC2-Backup-Execution-Role"
  }
}

resource "aws_iam_policy" "backup_least_privilege_policy" {
  name        = "ec2-backup-least-privilege-${var.environment}"
  description = "Least-privilege policy for EC2 backup script to access S3, CloudWatch, and SNS"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3BucketListing"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation"
        ]
        Resource = aws_s3_bucket.backup_bucket.arn
      },
      {
        Sid    = "S3ObjectBackupAndRestore"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:GetObjectVersion",
          "s3:AbortMultipartUpload"
        ]
        Resource = "${aws_s3_bucket.backup_bucket.arn}/*"
      },
      {
        Sid    = "CloudWatchMetricsPublishing"
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "AWSBackupRecovery/EC2"
          }
        }
      },
      {
        Sid    = "CloudWatchLogsStreaming"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams"
        ]
        Resource = "${aws_cloudwatch_log_group.backup_logs.arn}:*"
      },
      {
        Sid    = "SNSPublishAlerts"
        Effect = "Allow"
        Action = [
          "sns:Publish"
        ]
        Resource = aws_sns_topic.backup_alerts.arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "backup_attach" {
  role       = aws_iam_role.ec2_backup_role.name
  policy_arn = aws_iam_policy.backup_least_privilege_policy.arn
}

resource "aws_iam_instance_profile" "ec2_backup_profile" {
  name = "ec2-backup-instance-profile-${var.environment}"
  role = aws_iam_role.ec2_backup_role.name
}

# ------------------------------------------------------------------------------
# 5. Amazon EventBridge Scheduled Rule (AWS Native Scheduling)
# ------------------------------------------------------------------------------
resource "aws_cloudwatch_event_rule" "backup_schedule" {
  name                = "ec2-backup-schedule-${var.environment}"
  description         = "Triggers scheduled EC2 backup workflow"
  schedule_expression = var.backup_schedule_expression

  tags = {
    Name = "EC2-Backup-Schedule-Rule"
  }
}

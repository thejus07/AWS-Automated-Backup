output "s3_bucket_name" {
  description = "The name of the S3 bucket created for backups."
  value       = aws_s3_bucket.backup_bucket.id
}

output "s3_bucket_arn" {
  description = "The ARN of the S3 backup bucket."
  value       = aws_s3_bucket.backup_bucket.arn
}

output "iam_role_name" {
  description = "The name of the IAM Role for EC2 backup execution."
  value       = aws_iam_role.ec2_backup_role.name
}

output "iam_role_arn" {
  description = "The ARN of the IAM Role for EC2 backup execution."
  value       = aws_iam_role.ec2_backup_role.arn
}

output "iam_instance_profile_name" {
  description = "The IAM instance profile to attach to the EC2 instance."
  value       = aws_iam_instance_profile.ec2_backup_profile.name
}

output "sns_topic_arn" {
  description = "The ARN of the SNS alert topic."
  value       = aws_sns_topic.backup_alerts.arn
}

output "cloudwatch_log_group_name" {
  description = "The name of the CloudWatch Log Group for structured backup logs."
  value       = aws_cloudwatch_log_group.backup_logs.name
}

output "cloudwatch_alarm_name" {
  description = "The name of the CloudWatch alarm monitoring backup failures."
  value       = aws_cloudwatch_metric_alarm.backup_failure_alarm.alarm_name
}

"""
CloudWatch monitoring and alerting module for AWS Automated Backup & Recovery System.
Publishes custom metrics (success/failure, duration, size, file count) to Amazon CloudWatch,
streams logs to CloudWatch Logs, and triggers SNS notifications on failures.
"""

from datetime import datetime
import json
import os
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

from config.settings import setup_logger

logger = setup_logger("monitoring.cloudwatch")


class CloudWatchMonitor:
    """Publishes metrics and logs to Amazon CloudWatch and sends alerts via Amazon SNS."""

    def __init__(
        self,
        namespace: str = "AWSBackupRecovery/EC2",
        log_group_name: str = "/aws/ec2/backup-recovery",
        region: str = "us-east-1",
        cw_client: Optional[Any] = None,
        logs_client: Optional[Any] = None,
        sns_client: Optional[Any] = None,
        sns_topic_arn: Optional[str] = None
    ):
        self.namespace = namespace
        self.log_group_name = log_group_name
        self.region = region
        self.sns_topic_arn = sns_topic_arn or os.getenv("SNS_TOPIC_ARN", "")

        self.cw_client = cw_client or boto3.client("cloudwatch", region_name=region)
        self.logs_client = logs_client or boto3.client("logs", region_name=region)
        self.sns_client = sns_client or boto3.client("sns", region_name=region)

    def publish_metrics(
        self,
        success: bool,
        duration_seconds: float,
        size_bytes: int = 0,
        file_count: int = 0,
        dimensions: Optional[List[Dict[str, str]]] = None
    ) -> bool:
        """
        Publishes backup execution metrics to CloudWatch.
        """
        metric_dims = dimensions or [{"Name": "Service", "Value": "EC2Backup"}]
        timestamp = datetime.utcnow()

        metric_data = [
            {
                "MetricName": "BackupSuccess",
                "Dimensions": metric_dims,
                "Timestamp": timestamp,
                "Value": 1.0 if success else 0.0,
                "Unit": "Count",
            },
            {
                "MetricName": "BackupFailure",
                "Dimensions": metric_dims,
                "Timestamp": timestamp,
                "Value": 0.0 if success else 1.0,
                "Unit": "Count",
            },
            {
                "MetricName": "BackupDurationSeconds",
                "Dimensions": metric_dims,
                "Timestamp": timestamp,
                "Value": max(0.0, float(duration_seconds)),
                "Unit": "Seconds",
            },
        ]

        if success:
            metric_data.extend([
                {
                    "MetricName": "BackupSizeBytes",
                    "Dimensions": metric_dims,
                    "Timestamp": timestamp,
                    "Value": float(size_bytes),
                    "Unit": "Bytes",
                },
                {
                    "MetricName": "BackupFileCount",
                    "Dimensions": metric_dims,
                    "Timestamp": timestamp,
                    "Value": float(file_count),
                    "Unit": "Count",
                },
            ])

        try:
            self.cw_client.put_metric_data(
                Namespace=self.namespace,
                MetricData=metric_data
            )
            logger.info(f"Published {len(metric_data)} metrics to CloudWatch namespace '{self.namespace}'.")
            return True
        except (NoCredentialsError, ClientError) as e:
            logger.warning(f"Failed to publish metrics to CloudWatch: {e}")
            return False

    def ensure_log_group_and_stream(self, stream_name: str) -> bool:
        """
        Ensures the CloudWatch Log Group and Log Stream exist before writing events.
        """
        # Ensure Log Group
        try:
            self.logs_client.create_log_group(logGroupName=self.log_group_name)
            logger.info(f"Created CloudWatch Log Group: {self.log_group_name}")
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") != "ResourceAlreadyExistsException":
                logger.warning(f"Could not create log group {self.log_group_name}: {e}")
                return False

        # Ensure Log Stream
        try:
            self.logs_client.create_log_stream(
                logGroupName=self.log_group_name,
                logStreamName=stream_name
            )
            logger.info(f"Created CloudWatch Log Stream: {stream_name}")
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") != "ResourceAlreadyExistsException":
                logger.warning(f"Could not create log stream {stream_name}: {e}")
                return False

        return True

    def send_log_event(self, message: Dict[str, Any], stream_name: Optional[str] = None) -> bool:
        """
        Sends a structured JSON log event to CloudWatch Logs.
        """
        stream = stream_name or f"backup-{datetime.utcnow().strftime('%Y-%m-%d')}"
        if not self.ensure_log_group_and_stream(stream):
            return False

        event = {
            "timestamp": int(datetime.utcnow().timestamp() * 1000),
            "message": json.dumps(message),
        }

        try:
            self.logs_client.put_log_events(
                logGroupName=self.log_group_name,
                logStreamName=stream,
                logEvents=[event]
            )
            logger.info(f"Pushed log event to CloudWatch stream '{stream}'.")
            return True
        except (NoCredentialsError, ClientError) as e:
            logger.warning(f"Failed to push log events to CloudWatch Logs: {e}")
            return False

    def send_sns_alert(self, subject: str, message: str) -> bool:
        """
        Sends an alert notification via Amazon SNS if an SNS Topic ARN is configured.
        """
        if not self.sns_topic_arn:
            logger.debug("No SNS_TOPIC_ARN configured; skipping direct SNS notification.")
            return False

        try:
            self.sns_client.publish(
                TopicArn=self.sns_topic_arn,
                Subject=subject[:100],  # SNS subject max length 100 chars
                Message=message
            )
            logger.info(f"Sent SNS alert notification to {self.sns_topic_arn}")
            return True
        except (NoCredentialsError, ClientError) as e:
            logger.warning(f"Failed to publish SNS alert: {e}")
            return False

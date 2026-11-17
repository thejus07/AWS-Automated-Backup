"""
Configuration loader and structured logger setup for AWS Automated Backup & Recovery System.
Loads settings from environment variables and an optional .env file.
"""

from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass
class BackupConfig:
    """Strongly typed application configuration."""
    aws_region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))
    s3_bucket_name: str = field(default_factory=lambda: os.getenv("S3_BUCKET_NAME", ""))
    s3_backup_prefix: str = field(default_factory=lambda: os.getenv("S3_BACKUP_PREFIX", "backups"))
    backup_source_dir: str = field(default_factory=lambda: os.getenv("BACKUP_SOURCE_DIR", "/var/data"))
    backup_format: str = field(default_factory=lambda: os.getenv("BACKUP_FORMAT", "tar.gz").lower())
    restore_dir: str = field(default_factory=lambda: os.getenv("RESTORE_DIR", "/var/data_restored"))
    enable_cloudwatch_metrics: bool = field(
        default_factory=lambda: os.getenv("ENABLE_CLOUDWATCH_METRICS", "true").lower() in ("true", "1", "yes")
    )
    enable_cloudwatch_logs: bool = field(
        default_factory=lambda: os.getenv("ENABLE_CLOUDWATCH_LOGS", "true").lower() in ("true", "1", "yes")
    )
    cloudwatch_namespace: str = field(
        default_factory=lambda: os.getenv("CLOUDWATCH_NAMESPACE", "AWSBackupRecovery/EC2")
    )
    cloudwatch_log_group: str = field(
        default_factory=lambda: os.getenv("CLOUDWATCH_LOG_GROUP", "/aws/ec2/backup-recovery")
    )
    sns_topic_arn: str = field(default_factory=lambda: os.getenv("SNS_TOPIC_ARN", ""))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())
    log_file: str = field(default_factory=lambda: os.getenv("LOG_FILE", "logs/backup.log"))

    def validate(self, require_bucket: bool = True) -> None:
        """
        Validate configuration values.
        Raises ValueError if required parameters are missing or invalid.
        """
        if require_bucket and not self.s3_bucket_name.strip():
            raise ValueError(
                "S3_BUCKET_NAME is not set. Please set S3_BUCKET_NAME in your environment or .env file."
            )

        if self.backup_format not in ("tar.gz", "zip"):
            raise ValueError(
                f"Unsupported BACKUP_FORMAT '{self.backup_format}'. Supported formats: 'tar.gz', 'zip'."
            )

        valid_levels = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
        if self.log_level not in valid_levels:
            raise ValueError(
                f"Invalid LOG_LEVEL '{self.log_level}'. Must be one of: {', '.join(valid_levels)}"
            )


class StructuredJsonFormatter(logging.Formatter):
    """Formats log records as structured JSON for easy parsing by CloudWatch and log shippers."""

    def format(self, record: logging.LogRecord) -> str:
        log_record = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include exception tracebacks if present
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)

        # Include custom extra metadata passed to logger
        if hasattr(record, "extra_data") and isinstance(record.extra_data, dict):
            log_record.update(record.extra_data)

        return json.dumps(log_record)


def setup_logger(
    name: str = "backup_recovery",
    log_file: Optional[str] = None,
    log_level: str = "INFO"
) -> logging.Logger:
    """
    Configures and returns a logger instance with console and file output.
    """
    logger = logging.getLogger(name)

    # Avoid duplicate handlers if already configured
    if logger.hasHandlers():
        return logger

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(numeric_level)

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    json_formatter = StructuredJsonFormatter(datefmt="%Y-%m-%d %H:%M:%S")

    # Console Handler (Human-readable)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File Handler (Structured JSON)
    file_path = log_file or os.getenv("LOG_FILE", "logs/backup.log")
    if file_path:
        log_dir = Path(file_path).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(json_formatter)
        logger.addHandler(file_handler)

    return logger


def get_config() -> BackupConfig:
    """Helper to instantiate and return BackupConfig."""
    return BackupConfig()

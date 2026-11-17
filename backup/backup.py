#!/usr/bin/env python3
"""
Main backup orchestration script for AWS Automated Backup & Recovery System.
Discovers files, compresses into timestamped archives, uploads to S3,
publishes CloudWatch metrics, and alerts on failure.
"""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
from typing import Optional

# Ensure project root is in python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import BackupConfig, get_config, setup_logger
from backup.compression import compress_directory
from backup.upload import S3Uploader
from monitoring.cloudwatch import CloudWatchMonitor

logger = setup_logger("backup.main")


def run_backup(
    config: BackupConfig,
    source_dir_override: Optional[str] = None,
    bucket_override: Optional[str] = None,
    prefix_override: Optional[str] = None,
    format_override: Optional[str] = None,
    staging_dir: Optional[str] = None,
    keep_local: bool = False,
    force_upload: bool = False,
) -> bool:
    """
    Executes the end-to-end backup workflow.
    Returns True if backup succeeded, False otherwise.
    """
    start_time = time.time()
    source_dir = Path(source_dir_override or config.backup_source_dir).resolve()
    bucket_name = bucket_override or config.s3_bucket_name
    prefix = prefix_override or config.s3_backup_prefix
    archive_format = format_override or config.backup_format
    staging_path = Path(staging_dir or "backup_staging").resolve()

    logger.info("=" * 60)
    logger.info("Starting AWS Automated Backup Job")
    logger.info(f"Source Directory: {source_dir}")
    logger.info(f"Target S3 Bucket: {bucket_name}")
    logger.info(f"S3 Prefix:        {prefix}")
    logger.info(f"Archive Format:   {archive_format}")
    logger.info("=" * 60)

    # Validate bucket configuration
    if not bucket_name:
        error_msg = "S3 bucket name is required. Set S3_BUCKET_NAME in environment or use --bucket."
        logger.error(error_msg)
        return False

    if not source_dir.exists():
        error_msg = f"Backup source directory does not exist: {source_dir}"
        logger.error(error_msg)
        return False

    monitor = CloudWatchMonitor(
        namespace=config.cloudwatch_namespace,
        log_group_name=config.cloudwatch_log_group,
        region=config.aws_region,
        sns_topic_arn=config.sns_topic_arn,
    )

    archive_res = None
    upload_res = None
    success = False

    try:
        # Step 1: Compress files into timestamped archive
        timestamp = datetime.utcnow()
        archive_res = compress_directory(
            source_dir=source_dir,
            output_dir=staging_path,
            archive_format=archive_format,
            custom_timestamp=timestamp
        )

        # Step 2: Upload archive to S3
        uploader = S3Uploader(bucket_name=bucket_name, region=config.aws_region)
        s3_key = uploader.generate_s3_key(
            file_name=archive_res.archive_path.name,
            prefix=prefix,
            timestamp=timestamp
        )

        metadata = {
            "source-dir": str(source_dir),
            "file-count": str(archive_res.file_count),
            "uncompressed-bytes": str(archive_res.uncompressed_size_bytes),
            "compressed-bytes": str(archive_res.compressed_size_bytes),
        }

        upload_res = uploader.upload_file(
            file_path=archive_res.archive_path,
            s3_key=s3_key,
            sha256_checksum=archive_res.sha256_checksum,
            metadata=metadata,
            skip_duplicate=not force_upload
        )

        duration = time.time() - start_time
        success = True

        log_payload = {
            "status": "SUCCESS",
            "timestamp": timestamp.isoformat() + "Z",
            "source_dir": str(source_dir),
            "s3_uri": upload_res.s3_uri,
            "s3_bucket": upload_res.bucket,
            "s3_key": upload_res.key,
            "file_count": archive_res.file_count,
            "uncompressed_bytes": archive_res.uncompressed_size_bytes,
            "compressed_bytes": archive_res.compressed_size_bytes,
            "sha256": archive_res.sha256_checksum,
            "duration_seconds": round(duration, 2),
            "is_duplicate": upload_res.is_duplicate,
        }
        logger.info(f"Backup completed successfully: {json.dumps(log_payload)}")

        # Step 3: Publish metrics to CloudWatch
        if config.enable_cloudwatch_metrics:
            monitor.publish_metrics(
                success=True,
                duration_seconds=duration,
                size_bytes=archive_res.compressed_size_bytes,
                file_count=archive_res.file_count
            )

        # Step 4: Stream log event to CloudWatch Logs
        if config.enable_cloudwatch_logs:
            monitor.send_log_event(log_payload)

        return True

    except Exception as e:
        duration = time.time() - start_time
        logger.exception(f"Backup job encountered an error: {e}")

        # Publish failure metric
        if config.enable_cloudwatch_metrics:
            monitor.publish_metrics(
                success=False,
                duration_seconds=duration
            )

        # Send failure alert to CloudWatch Logs and SNS
        err_payload = {
            "status": "FAILED",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "error": str(e),
            "source_dir": str(source_dir),
            "target_bucket": bucket_name,
            "duration_seconds": round(duration, 2),
        }
        if config.enable_cloudwatch_logs:
            monitor.send_log_event(err_payload)

        monitor.send_sns_alert(
            subject=f"ALERT: AWS EC2 Backup Job Failed ({bucket_name})",
            message=(
                f"EC2 Backup job failed.\n"
                f"Error: {e}\n"
                f"Source: {source_dir}\n"
                f"Target Bucket: {bucket_name}\n"
                f"Time: {datetime.utcnow().isoformat()}Z"
            )
        )
        return False

    finally:
        # Clean up local archive in staging unless requested to keep
        if archive_res and archive_res.archive_path.exists() and not keep_local:
            try:
                archive_res.archive_path.unlink()
                logger.info(f"Cleaned up local staging archive: {archive_res.archive_path}")
            except OSError as cleanup_err:
                logger.warning(f"Could not delete staging archive {archive_res.archive_path}: {cleanup_err}")


def build_cli_parser() -> argparse.ArgumentParser:
    """Builds the command-line argument parser."""
    parser = argparse.ArgumentParser(
        description="AWS Automated Backup System: Backup local directories to S3 with compression and monitoring."
    )
    parser.add_argument(
        "--source",
        "-s",
        help="Path to the directory to back up (overrides BACKUP_SOURCE_DIR).",
    )
    parser.add_argument(
        "--bucket",
        "-b",
        help="Target Amazon S3 bucket name (overrides S3_BUCKET_NAME).",
    )
    parser.add_argument(
        "--prefix",
        "-p",
        help="S3 key prefix for storing backups (overrides S3_BACKUP_PREFIX).",
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["tar.gz", "zip"],
        help="Compression format for archive (tar.gz or zip).",
    )
    parser.add_argument(
        "--staging-dir",
        help="Local directory for temporary archive creation before upload.",
    )
    parser.add_argument(
        "--keep-local",
        action="store_true",
        help="Retain the local archive copy after successful upload to S3.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force upload even if an identical archive hash already exists in S3.",
    )
    return parser


def main() -> None:
    """CLI entrypoint."""
    parser = build_cli_parser()
    args = parser.parse_args()

    config = get_config()
    success = run_backup(
        config=config,
        source_dir_override=args.source,
        bucket_override=args.bucket,
        prefix_override=args.prefix,
        format_override=args.format,
        staging_dir=args.staging_dir,
        keep_local=args.keep_local,
        force_upload=args.force,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

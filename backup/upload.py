"""
S3 upload module for AWS Automated Backup & Recovery System.
Uploads compressed archives to S3 organized in date-based prefixes, attaches metadata,
and checks for duplicate uploads using SHA256 checksums.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

from config.settings import setup_logger

logger = setup_logger("backup.upload")


@dataclass
class UploadResult:
    """Contains metadata about a completed S3 upload."""
    s3_uri: str
    bucket: str
    key: str
    size_bytes: int
    sha256_checksum: str
    upload_time_seconds: float
    is_duplicate: bool = False


class S3Uploader:
    """Handles secure uploading of backup archives to Amazon S3."""

    def __init__(self, bucket_name: str, region: str = "us-east-1", s3_client: Optional[Any] = None):
        self.bucket_name = bucket_name
        self.region = region
        self.s3_client = s3_client or boto3.client("s3", region_name=region)

    def generate_s3_key(
        self,
        file_name: str,
        prefix: str = "backups",
        timestamp: Optional[datetime] = None
    ) -> str:
        """
        Generates a date-partitioned S3 key:
        <prefix>/YYYY/MM/DD/<filename>
        """
        ts = timestamp or datetime.utcnow()
        year = ts.strftime("%Y")
        month = ts.strftime("%m")
        day = ts.strftime("%d")

        clean_prefix = prefix.strip("/")
        if clean_prefix:
            return f"{clean_prefix}/{year}/{month}/{day}/{file_name}"
        return f"{year}/{month}/{day}/{file_name}"

    def check_duplicate(self, key: str, expected_checksum: str) -> bool:
        """
        Checks if an object already exists at the given S3 key with identical SHA256 checksum.
        """
        try:
            response = self.s3_client.head_object(Bucket=self.bucket_name, Key=key)
            existing_checksum = response.get("Metadata", {}).get("sha256")
            if existing_checksum and existing_checksum == expected_checksum:
                logger.info(f"Duplicate found: Object with matching SHA256 already exists at s3://{self.bucket_name}/{key}")
                return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            # 404 / NotFound means the object does not exist, which is expected
            if error_code not in ("404", "NoSuchKey", "NotFound"):
                logger.warning(f"Error checking for duplicate at s3://{self.bucket_name}/{key}: {e}")
        return False

    def upload_file(
        self,
        file_path: Path,
        s3_key: str,
        sha256_checksum: str,
        metadata: Optional[Dict[str, str]] = None,
        skip_duplicate: bool = True
    ) -> UploadResult:
        """
        Uploads a local archive file to Amazon S3 with SSE-S3 encryption and custom metadata.
        """
        local_path = Path(file_path)
        if not local_path.is_file():
            raise FileNotFoundError(f"Local file does not exist: {local_path}")

        file_size = local_path.stat().st_size

        # Check for duplicate
        if skip_duplicate and self.check_duplicate(s3_key, sha256_checksum):
            return UploadResult(
                s3_uri=f"s3://{self.bucket_name}/{s3_key}",
                bucket=self.bucket_name,
                key=s3_key,
                size_bytes=file_size,
                sha256_checksum=sha256_checksum,
                upload_time_seconds=0.0,
                is_duplicate=True
            )

        upload_metadata = {
            "sha256": sha256_checksum,
            "original-filename": local_path.name,
            "uploaded-at": datetime.utcnow().isoformat() + "Z",
        }
        if metadata:
            upload_metadata.update(metadata)

        extra_args: Dict[str, Any] = {
            "ServerSideEncryption": "AES256",
            "Metadata": upload_metadata,
        }

        logger.info(f"Uploading {local_path.name} ({file_size} bytes) to s3://{self.bucket_name}/{s3_key}...")
        start_time = datetime.utcnow()

        try:
            self.s3_client.upload_file(
                Filename=str(local_path),
                Bucket=self.bucket_name,
                Key=s3_key,
                ExtraArgs=extra_args
            )
        except NoCredentialsError as e:
            logger.error("AWS credentials not found. Ensure an IAM role is attached or AWS credentials are set.")
            raise e
        except ClientError as e:
            logger.error(f"Failed to upload to S3: {e}")
            raise e

        duration = (datetime.utcnow() - start_time).total_seconds()
        logger.info(f"Successfully uploaded to s3://{self.bucket_name}/{s3_key} in {duration:.2f}s")

        return UploadResult(
            s3_uri=f"s3://{self.bucket_name}/{s3_key}",
            bucket=self.bucket_name,
            key=s3_key,
            size_bytes=file_size,
            sha256_checksum=sha256_checksum,
            upload_time_seconds=duration,
            is_duplicate=False
        )

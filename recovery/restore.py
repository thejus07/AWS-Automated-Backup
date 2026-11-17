#!/usr/bin/env python3
"""
Disaster Recovery & Restore module for AWS Automated Backup & Recovery System.
Lists available backup archives from Amazon S3, downloads, verifies integrity (SHA256),
checks archive structure, and safely extracts files without unauthorized overwriting.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import sys
import tarfile
from typing import Any, Dict, List, Optional, Tuple
import zipfile

# Ensure project root is in python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

from config.settings import BackupConfig, get_config, setup_logger
from backup.compression import calculate_sha256

logger = setup_logger("recovery.restore")


@dataclass
class S3BackupSummary:
    """Summary of an available backup archive in S3."""
    key: str
    size_bytes: int
    last_modified: datetime
    sha256_checksum: Optional[str] = None
    file_count: Optional[int] = None

    @property
    def size_readable(self) -> str:
        """Returns human-readable size string."""
        size = float(self.size_bytes)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"


class S3Restorer:
    """Handles S3 listing, downloading, integrity checks, and secure extraction."""

    def __init__(self, bucket_name: str, region: str = "us-east-1", s3_client: Optional[Any] = None):
        self.bucket_name = bucket_name
        self.region = region
        self.s3_client = s3_client or boto3.client("s3", region_name=region)

    def list_backups(self, prefix: str = "backups") -> List[S3BackupSummary]:
        """
        Lists all available backup archives in S3 bucket sorted chronologically (newest first).
        """
        backups: List[S3BackupSummary] = []
        paginator = self.s3_client.get_paginator("list_objects_v2")

        try:
            for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if not (key.endswith(".tar.gz") or key.endswith(".zip") or key.endswith(".tgz")):
                        continue

                    # Retrieve metadata if available
                    sha256_val = None
                    file_count = None
                    try:
                        head = self.s3_client.head_object(Bucket=self.bucket_name, Key=key)
                        meta = head.get("Metadata", {})
                        sha256_val = meta.get("sha256")
                        if meta.get("file-count"):
                            file_count = int(meta["file-count"])
                    except ClientError:
                        pass

                    backups.append(
                        S3BackupSummary(
                            key=key,
                            size_bytes=obj["Size"],
                            last_modified=obj["LastModified"],
                            sha256_checksum=sha256_val,
                            file_count=file_count,
                        )
                    )
        except (NoCredentialsError, ClientError) as e:
            logger.error(f"Failed to list backups from S3 bucket {self.bucket_name}: {e}")
            raise e

        # Sort descending by last modified date (most recent first)
        backups.sort(key=lambda b: b.last_modified, reverse=True)
        return backups

    def download_backup(
        self,
        s3_key: str,
        download_dir: Path
    ) -> Tuple[Path, Optional[str]]:
        """
        Downloads a backup archive from S3 into download_dir.
        Returns the downloaded local file path and expected SHA256 checksum from S3 metadata.
        """
        download_dir = Path(download_dir).resolve()
        download_dir.mkdir(parents=True, exist_ok=True)

        filename = Path(s3_key).name
        target_path = download_dir / filename

        logger.info(f"Downloading s3://{self.bucket_name}/{s3_key} to {target_path}...")

        try:
            head = self.s3_client.head_object(Bucket=self.bucket_name, Key=s3_key)
            expected_sha256 = head.get("Metadata", {}).get("sha256")

            self.s3_client.download_file(
                Bucket=self.bucket_name,
                Key=s3_key,
                Filename=str(target_path)
            )
            logger.info(f"Download complete: {target_path} ({target_path.stat().st_size} bytes)")
            return target_path, expected_sha256
        except ClientError as e:
            logger.error(f"Failed to download s3://{self.bucket_name}/{s3_key}: {e}")
            raise e

    def validate_archive(self, archive_path: Path, expected_sha256: Optional[str] = None) -> bool:
        """
        Validates the integrity of the downloaded archive.
        Checks SHA256 checksum and archive structure (tar or zip).
        """
        if not archive_path.exists():
            logger.error(f"Archive file does not exist: {archive_path}")
            return False

        # 1. SHA256 Validation
        if expected_sha256:
            actual_sha256 = calculate_sha256(archive_path)
            if actual_sha256.lower() != expected_sha256.lower():
                logger.error(
                    f"Checksum mismatch for {archive_path.name}!\n"
                    f"Expected: {expected_sha256}\n"
                    f"Actual:   {actual_sha256}"
                )
                return False
            logger.info("Checksum validation passed (SHA256 matches S3 metadata).")

        # 2. Archive structure validation
        if archive_path.name.endswith((".tar.gz", ".tgz")):
            if not tarfile.is_tarfile(archive_path):
                logger.error(f"File {archive_path.name} is not a valid tar archive!")
                return False
        elif archive_path.name.endswith(".zip"):
            if not zipfile.is_zipfile(archive_path):
                logger.error(f"File {archive_path.name} is not a valid zip archive!")
                return False
        else:
            logger.warning(f"Unknown archive extension for {archive_path.name}, skipping structural check.")

        logger.info(f"Archive validation successful for {archive_path.name}.")
        return True

    def check_for_collisions(self, archive_path: Path, target_dir: Path) -> List[Path]:
        """
        Inspects archive members and checks if any would overwrite existing files in target_dir.
        """
        target_dir = Path(target_dir).resolve()
        collisions: List[Path] = []

        if archive_path.name.endswith((".tar.gz", ".tgz")):
            with tarfile.open(archive_path, "r:gz") as tar:
                for member in tar.getmembers():
                    if member.isfile():
                        dest = target_dir / member.name
                        if dest.exists():
                            collisions.append(dest)
        elif archive_path.name.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                for name in zf.namelist():
                    dest = target_dir / name
                    if dest.exists() and not dest.is_dir():
                        collisions.append(dest)

        return collisions

    def extract_archive(
        self,
        archive_path: Path,
        target_dir: Path,
        force: bool = False
    ) -> int:
        """
        Safely extracts archive to target_dir.
        Protects against Directory Traversal (Zip Slip) and file overwrites.
        Returns the number of extracted files.
        """
        target_dir = Path(target_dir).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        collisions = self.check_for_collisions(archive_path, target_dir)
        if collisions and not force:
            logger.error(
                f"Extraction aborted! Found {len(collisions)} existing files in target directory '{target_dir}'.\n"
                f"Example collisions: {[str(c.relative_to(target_dir)) for c in collisions[:5]]}\n"
                f"To overwrite existing files, run with --force flag."
            )
            raise FileExistsError(
                f"Refusing to overwrite {len(collisions)} existing files without --force flag."
            )

        extracted_count = 0
        if archive_path.name.endswith((".tar.gz", ".tgz")):
            with tarfile.open(archive_path, "r:gz") as tar:
                for member in tar.getmembers():
                    # Security check: Zip Slip prevention
                    dest = (target_dir / member.name).resolve()
                    if not str(dest).startswith(str(target_dir)):
                        raise SecurityError(f"Attempted Path Traversal in Tar member: {member.name}")

                    tar.extract(member, path=target_dir)
                    if member.isfile():
                        extracted_count += 1
        elif archive_path.name.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                for member_name in zf.namelist():
                    # Security check: Zip Slip prevention
                    dest = (target_dir / member_name).resolve()
                    if not str(dest).startswith(str(target_dir)):
                        raise SecurityError(f"Attempted Path Traversal in Zip member: {member_name}")

                    zf.extract(member_name, path=target_dir)
                    if dest.is_file():
                        extracted_count += 1

        logger.info(f"Successfully extracted {extracted_count} files to {target_dir}")
        return extracted_count


class SecurityError(Exception):
    """Raised when an archive member attempts a path traversal attack."""
    pass


# Convenience functions
from typing import Tuple


def list_backups(config: BackupConfig, prefix: Optional[str] = None) -> List[S3BackupSummary]:
    """Helper to list backups using configuration."""
    restorer = S3Restorer(bucket_name=config.s3_bucket_name, region=config.aws_region)
    return restorer.list_backups(prefix=prefix or config.s3_backup_prefix)


def restore_backup(
    config: BackupConfig,
    s3_key: str,
    target_dir: Optional[str] = None,
    staging_dir: Optional[str] = None,
    force: bool = False
) -> bool:
    """
    Downloads, validates, and extracts a specific backup archive.
    """
    restorer = S3Restorer(bucket_name=config.s3_bucket_name, region=config.aws_region)
    dest_dir = Path(target_dir or config.restore_dir).resolve()
    temp_dir = Path(staging_dir or "restore_staging").resolve()

    logger.info(f"Starting restoration for {s3_key}")
    logger.info(f"Target directory: {dest_dir}")

    downloaded_archive = None
    try:
        downloaded_archive, expected_sha256 = restorer.download_backup(s3_key, temp_dir)

        # Validate
        if not restorer.validate_archive(downloaded_archive, expected_sha256):
            logger.error("Archive validation failed. Aborting restore.")
            return False

        # Extract
        file_count = restorer.extract_archive(downloaded_archive, dest_dir, force=force)
        logger.info(f"Restore succeeded! Restored {file_count} files into {dest_dir}")
        return True

    except Exception as e:
        logger.exception(f"Restore operation failed: {e}")
        return False
    finally:
        if downloaded_archive and downloaded_archive.exists():
            try:
                downloaded_archive.unlink()
                logger.info(f"Cleaned up temporary downloaded archive: {downloaded_archive}")
            except OSError as err:
                logger.warning(f"Failed to remove temp file {downloaded_archive}: {err}")


def print_backups_table(backups: List[S3BackupSummary]) -> None:
    """Displays backups in a clear formatted table."""
    if not backups:
        print("\nNo backup archives found in S3 bucket.")
        return

    print("\n" + "=" * 90)
    print(f"{'#':<3} | {'Date (UTC)':<20} | {'Size':<10} | {'S3 Key'}")
    print("-" * 90)
    for idx, b in enumerate(backups, 1):
        dt_str = b.last_modified.strftime("%Y-%m-%d %H:%M:%S")
        print(f"{idx:<3} | {dt_str:<20} | {b.size_readable:<10} | {b.key}")
    print("=" * 90 + "\n")


def build_cli_parser() -> argparse.ArgumentParser:
    """CLI argument parser for restore operations."""
    parser = argparse.ArgumentParser(
        description="AWS Automated Recovery System: List, download, validate, and restore S3 backup archives."
    )
    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="List available backups in the configured S3 bucket.",
    )
    parser.add_argument(
        "--key",
        "-k",
        help="Specific S3 key to download and restore.",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Restore the most recent available backup automatically.",
    )
    parser.add_argument(
        "--target-dir",
        "-t",
        help="Directory to extract restored files into (overrides RESTORE_DIR).",
    )
    parser.add_argument(
        "--bucket",
        "-b",
        help="S3 bucket name (overrides S3_BUCKET_NAME).",
    )
    parser.add_argument(
        "--prefix",
        "-p",
        help="S3 backup prefix (overrides S3_BACKUP_PREFIX).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Explicitly allow overwriting existing files in the target directory.",
    )
    return parser


def main() -> None:
    """CLI entrypoint for recovery."""
    parser = build_cli_parser()
    args = parser.parse_args()

    config = get_config()
    if args.bucket:
        config.s3_bucket_name = args.bucket
    if args.prefix:
        config.s3_backup_prefix = args.prefix

    if not config.s3_bucket_name:
        print("Error: S3_BUCKET_NAME is not configured. Set S3_BUCKET_NAME or use --bucket.")
        sys.exit(1)

    restorer = S3Restorer(bucket_name=config.s3_bucket_name, region=config.aws_region)

    if args.list:
        backups = restorer.list_backups(prefix=config.s3_backup_prefix)
        print_backups_table(backups)
        sys.exit(0)

    selected_key = args.key

    if args.latest:
        backups = restorer.list_backups(prefix=config.s3_backup_prefix)
        if not backups:
            print("Error: No backups found in S3 to restore.")
            sys.exit(1)
        selected_key = backups[0].key
        print(f"Selected latest backup: {selected_key}")

    # If neither --key nor --latest passed, prompt interactively
    if not selected_key:
        backups = restorer.list_backups(prefix=config.s3_backup_prefix)
        if not backups:
            print("No backups found in S3 bucket.")
            sys.exit(0)

        print_backups_table(backups)
        choice = input("Enter the number of the backup to restore (or 'q' to quit): ").strip()
        if choice.lower() in ("q", "quit", ""):
            print("Restoration cancelled.")
            sys.exit(0)

        try:
            choice_idx = int(choice) - 1
            if 0 <= choice_idx < len(backups):
                selected_key = backups[choice_idx].key
            else:
                print("Invalid selection.")
                sys.exit(1)
        except ValueError:
            print("Invalid input. Must be an integer.")
            sys.exit(1)

    # Perform restoration
    success = restore_backup(
        config=config,
        s3_key=selected_key,
        target_dir=args.target_dir,
        force=args.force,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

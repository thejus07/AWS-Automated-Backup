"""
Archive compression module for AWS Automated Backup & Recovery System.
Supports TAR.GZ and ZIP compression with checksum generation and archive statistics.
"""

from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
import tarfile
from typing import List, Optional, Tuple
import zipfile

from config.settings import setup_logger

logger = setup_logger("backup.compression")


@dataclass
class ArchiveResult:
    """Contains metadata about a completed compression archive."""
    archive_path: Path
    format: str
    file_count: int
    uncompressed_size_bytes: int
    compressed_size_bytes: int
    sha256_checksum: str
    timestamp: datetime


def calculate_sha256(file_path: Path) -> str:
    """Calculates SHA256 checksum of a file for integrity verification."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(65536), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def discover_files(
    source_dir: Path,
    exclude_patterns: Optional[List[str]] = None
) -> Tuple[List[Path], int]:
    """
    Recursively scans the source directory to discover all files.
    Returns a list of file paths and total uncompressed size in bytes.
    """
    if not source_dir.exists():
        raise FileNotFoundError(f"Source directory does not exist: {source_dir}")
    if not source_dir.is_dir():
        raise NotADirectoryError(f"Source path is not a directory: {source_dir}")

    exclude_patterns = exclude_patterns or [".tmp", ".swp", ".pyc", "__pycache__"]
    files: List[Path] = []
    total_size = 0

    for root, _, filenames in os.walk(source_dir):
        for name in filenames:
            file_path = Path(root) / name
            # Check exclusions
            if any(pattern in str(file_path) for pattern in exclude_patterns):
                continue
            if file_path.is_file() and not file_path.is_symlink():
                files.append(file_path)
                try:
                    total_size += file_path.stat().st_size
                except OSError as e:
                    logger.warning(f"Could not read size of {file_path}: {e}")

    return files, total_size


def create_tar_archive(
    source_dir: Path,
    output_path: Path,
    files: List[Path]
) -> None:
    """Creates a compressed tar.gz archive preserving relative file structure."""
    with tarfile.open(output_path, "w:gz") as tar:
        for file_path in files:
            arcname = file_path.relative_to(source_dir)
            tar.add(file_path, arcname=str(arcname))


def create_zip_archive(
    source_dir: Path,
    output_path: Path,
    files: List[Path]
) -> None:
    """Creates a compressed zip archive preserving relative file structure."""
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in files:
            arcname = file_path.relative_to(source_dir)
            zf.write(file_path, arcname=str(arcname))


def compress_directory(
    source_dir: Path,
    output_dir: Path,
    archive_format: str = "tar.gz",
    custom_timestamp: Optional[datetime] = None,
    exclude_patterns: Optional[List[str]] = None
) -> ArchiveResult:
    """
    Scans a directory, compresses its contents into a timestamped archive,
    calculates checksum and size statistics, and returns an ArchiveResult.
    """
    source_path = Path(source_dir).resolve()
    target_dir = Path(output_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    files, uncompressed_size = discover_files(source_path, exclude_patterns=exclude_patterns)
    if not files:
        logger.warning(f"No files found in source directory: {source_path}")

    ts = custom_timestamp or datetime.utcnow()
    timestamp_str = ts.strftime("%Y-%m-%d_%H%M%S")

    normalized_format = archive_format.lower()
    if normalized_format in ("tar.gz", "tgz"):
        archive_name = f"backup_{timestamp_str}.tar.gz"
        archive_path = target_dir / archive_name
        logger.info(f"Creating TAR.GZ archive at {archive_path} with {len(files)} files...")
        create_tar_archive(source_path, archive_path, files)
    elif normalized_format == "zip":
        archive_name = f"backup_{timestamp_str}.zip"
        archive_path = target_dir / archive_name
        logger.info(f"Creating ZIP archive at {archive_path} with {len(files)} files...")
        create_zip_archive(source_path, archive_path, files)
    else:
        raise ValueError(f"Unsupported archive format: {archive_format}. Use 'tar.gz' or 'zip'.")

    compressed_size = archive_path.stat().st_size
    checksum = calculate_sha256(archive_path)

    logger.info(
        f"Archive created: {archive_path.name} "
        f"({compressed_size} bytes compressed, {uncompressed_size} bytes uncompressed, SHA256: {checksum[:12]}...)"
    )

    return ArchiveResult(
        archive_path=archive_path,
        format=normalized_format,
        file_count=len(files),
        uncompressed_size_bytes=uncompressed_size,
        compressed_size_bytes=compressed_size,
        sha256_checksum=checksum,
        timestamp=ts,
    )

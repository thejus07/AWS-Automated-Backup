"""
Unit tests for disaster recovery functionality: S3 backup listing, archive downloading,
SHA256 integrity verification, safe extraction, collision detection, and Zip Slip prevention.
All AWS calls are mocked using unittest.mock to run 100% locally with zero cloud charges.
"""

from datetime import datetime
from pathlib import Path
import shutil
import tarfile
import unittest
from unittest.mock import MagicMock, patch
import zipfile

from botocore.exceptions import ClientError

from backup.compression import calculate_sha256
from config.settings import BackupConfig
from recovery.restore import S3BackupSummary, S3Restorer, SecurityError, restore_backup


class TestRecoveryListingAndDownload(unittest.TestCase):
    """Tests for S3 listing and downloading."""

    def setUp(self):
        self.mock_s3 = MagicMock()
        self.restorer = S3Restorer(
            bucket_name="test-bucket",
            region="us-east-1",
            s3_client=self.mock_s3
        )
        self.staging_dir = Path("tests/tmp_recovery_staging")
        self.staging_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self.staging_dir.exists():
            shutil.rmtree(self.staging_dir, ignore_errors=True)

    def test_list_backups_sorted(self):
        # Mock S3 list_objects_v2 paginator
        paginator = MagicMock()
        self.mock_s3.get_paginator.return_value = paginator

        paginator.paginate.return_value = [
            {
                "Contents": [
                    {
                        "Key": "backups/2026/11/15/backup_old.tar.gz",
                        "Size": 1024,
                        "LastModified": datetime(2026, 11, 15, 10, 0, 0),
                    },
                    {
                        "Key": "backups/2026/11/17/backup_new.tar.gz",
                        "Size": 2048,
                        "LastModified": datetime(2026, 11, 17, 10, 0, 0),
                    },
                    {
                        "Key": "backups/ignored_text.txt",
                        "Size": 50,
                        "LastModified": datetime(2026, 11, 17, 11, 0, 0),
                    },
                ]
            }
        ]

        # Mock head_object for metadata
        self.mock_s3.head_object.return_value = {
            "Metadata": {"sha256": "fakehash", "file-count": "5"}
        }

        backups = self.restorer.list_backups(prefix="backups")
        self.assertEqual(len(backups), 2)
        # Verify sorted newest first
        self.assertEqual(backups[0].key, "backups/2026/11/17/backup_new.tar.gz")
        self.assertEqual(backups[1].key, "backups/2026/11/15/backup_old.tar.gz")
        self.assertEqual(backups[0].file_count, 5)

    def test_download_backup_success(self):
        self.mock_s3.head_object.return_value = {
            "Metadata": {"sha256": "expected_checksum_123"}
        }

        # Mock download_file creating a dummy local file
        def fake_download(Bucket, Key, Filename):
            Path(Filename).write_text("downloaded dummy content", encoding="utf-8")

        self.mock_s3.download_file.side_effect = fake_download

        dest_file, sha256 = self.restorer.download_backup(
            "backups/2026/11/17/backup.tar.gz",
            self.staging_dir
        )

        self.assertTrue(dest_file.exists())
        self.assertEqual(dest_file.name, "backup.tar.gz")
        self.assertEqual(sha256, "expected_checksum_123")


class TestArchiveValidationAndExtraction(unittest.TestCase):
    """Tests for archive validation, collision prevention, and extraction."""

    def setUp(self):
        self.mock_s3 = MagicMock()
        self.restorer = S3Restorer("test-bucket", s3_client=self.mock_s3)

        self.test_root = Path("tests/tmp_recovery_test")
        self.test_root.mkdir(parents=True, exist_ok=True)
        self.target_dir = self.test_root / "restored_output"
        self.target_dir.mkdir(parents=True, exist_ok=True)

        # Create a valid tar.gz archive
        self.tar_path = self.test_root / "valid_sample.tar.gz"
        with tarfile.open(self.tar_path, "w:gz") as tar:
            sample_file = self.test_root / "payload.txt"
            sample_file.write_text("Restored Payload Content", encoding="utf-8")
            tar.add(sample_file, arcname="payload.txt")
            sample_file.unlink()

        self.valid_sha256 = calculate_sha256(self.tar_path)

    def tearDown(self):
        if self.test_root.exists():
            shutil.rmtree(self.test_root, ignore_errors=True)

    def test_validate_archive_success(self):
        is_valid = self.restorer.validate_archive(self.tar_path, expected_sha256=self.valid_sha256)
        self.assertTrue(is_valid)

    def test_validate_archive_checksum_mismatch(self):
        is_valid = self.restorer.validate_archive(self.tar_path, expected_sha256="wrong_checksum_abc")
        self.assertFalse(is_valid)

    def test_validate_corrupted_archive(self):
        corrupt_file = self.test_root / "corrupted.tar.gz"
        corrupt_file.write_bytes(b"garbage not a tar")
        is_valid = self.restorer.validate_archive(corrupt_file, expected_sha256=None)
        self.assertFalse(is_valid)

    def test_extract_archive_clean(self):
        count = self.restorer.extract_archive(self.tar_path, self.target_dir, force=False)
        self.assertEqual(count, 1)
        restored_file = self.target_dir / "payload.txt"
        self.assertTrue(restored_file.exists())
        self.assertEqual(restored_file.read_text(encoding="utf-8"), "Restored Payload Content")

    def test_collision_detection_and_safe_refusal(self):
        # Pre-create the colliding file
        colliding_file = self.target_dir / "payload.txt"
        colliding_file.write_text("Original Existing File", encoding="utf-8")

        # Extraction should refuse without --force
        with self.assertRaises(FileExistsError):
            self.restorer.extract_archive(self.tar_path, self.target_dir, force=False)

        # Original content should be untouched
        self.assertEqual(colliding_file.read_text(encoding="utf-8"), "Original Existing File")

    def test_extract_with_force_overwrites(self):
        colliding_file = self.target_dir / "payload.txt"
        colliding_file.write_text("Original Existing File", encoding="utf-8")

        count = self.restorer.extract_archive(self.tar_path, self.target_dir, force=True)
        self.assertEqual(count, 1)
        self.assertEqual(colliding_file.read_text(encoding="utf-8"), "Restored Payload Content")

    def test_zip_slip_security_prevention(self):
        # Create a malicious tar archive attempting to escape target_dir via ../../../
        malicious_tar = self.test_root / "malicious.tar.gz"
        with tarfile.open(malicious_tar, "w:gz") as tar:
            import io
            data = b"malicious content"
            tarinfo = tarfile.TarInfo(name="../../escaped.txt")
            tarinfo.size = len(data)
            tar.addfile(tarinfo, io.BytesIO(data))

        with self.assertRaises(SecurityError):
            self.restorer.extract_archive(malicious_tar, self.target_dir, force=True)


class TestEndToEndRestore(unittest.TestCase):
    """End-to-end restore workflow integration tests."""

    @patch("recovery.restore.S3Restorer")
    def test_restore_backup_workflow(self, mock_restorer_cls):
        mock_restorer = MagicMock()
        mock_restorer_cls.return_value = mock_restorer

        dummy_path = Path("tests/tmp_dummy_restore.tar.gz")
        dummy_path.write_bytes(b"dummy")

        mock_restorer.download_backup.return_value = (dummy_path, "dummy_hash")
        mock_restorer.validate_archive.return_value = True
        mock_restorer.extract_archive.return_value = 5

        config = BackupConfig(s3_bucket_name="test-bucket")
        success = restore_backup(
            config=config,
            s3_key="backups/2026/11/17/backup.tar.gz",
            target_dir="tests/tmp_restore_out",
            force=True
        )

        self.assertTrue(success)
        mock_restorer.validate_archive.assert_called_once()
        mock_restorer.extract_archive.assert_called_once()


if __name__ == "__main__":
    unittest.main()

"""
Unit tests for backup functionality: configuration, compression, S3 upload, and CloudWatch metrics.
All AWS calls are mocked using unittest.mock to run 100% locally with zero cloud charges.
"""

from datetime import datetime
from pathlib import Path
import tarfile
import unittest
from unittest.mock import MagicMock, patch
import zipfile

from botocore.exceptions import ClientError

from backup.backup import run_backup
from backup.compression import (
    calculate_sha256,
    compress_directory,
    discover_files,
)
from backup.upload import S3Uploader, UploadResult
from config.settings import BackupConfig
from monitoring.cloudwatch import CloudWatchMonitor


class TestBackupConfiguration(unittest.TestCase):
    """Tests for configuration validation."""

    def test_config_validation_success(self):
        config = BackupConfig(
            s3_bucket_name="my-test-bucket",
            backup_source_dir="/var/data",
            backup_format="tar.gz"
        )
        # Should not raise
        config.validate()

    def test_config_validation_missing_bucket(self):
        config = BackupConfig(s3_bucket_name="")
        with self.assertRaises(ValueError) as ctx:
            config.validate(require_bucket=True)
        self.assertIn("S3_BUCKET_NAME is not set", str(ctx.exception))

    def test_config_validation_invalid_format(self):
        config = BackupConfig(s3_bucket_name="test-bucket", backup_format="rar")
        with self.assertRaises(ValueError) as ctx:
            config.validate()
        self.assertIn("Unsupported BACKUP_FORMAT", str(ctx.exception))

    def test_config_validation_invalid_log_level(self):
        config = BackupConfig(s3_bucket_name="test-bucket", log_level="INVALID")
        with self.assertRaises(ValueError) as ctx:
            config.validate()
        self.assertIn("Invalid LOG_LEVEL", str(ctx.exception))


class TestCompression(unittest.TestCase):
    """Tests for file discovery and compression."""

    def setUp(self):
        self.test_dir = Path("tests/tmp_backup_source")
        self.output_dir = Path("tests/tmp_backup_output")
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Create sample files
        (self.test_dir / "file1.txt").write_text("Hello World 1", encoding="utf-8")
        sub = self.test_dir / "subdir"
        sub.mkdir(exist_ok=True)
        (sub / "file2.txt").write_text("Hello Subdirectory 2", encoding="utf-8")
        # Temporary file that should be excluded
        (self.test_dir / "ignored.tmp").write_text("ignore me", encoding="utf-8")

    def tearDown(self):
        import shutil
        for path in [self.test_dir, self.output_dir]:
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)

    def test_discover_files(self):
        files, total_size = discover_files(self.test_dir)
        file_names = [f.name for f in files]
        self.assertIn("file1.txt", file_names)
        self.assertIn("file2.txt", file_names)
        self.assertNotIn("ignored.tmp", file_names)
        self.assertGreater(total_size, 0)

    def test_discover_missing_directory(self):
        with self.assertRaises(FileNotFoundError):
            discover_files(Path("tests/non_existent_dir_12345"))

    def test_compress_tar_gz(self):
        fixed_time = datetime(2026, 11, 17, 10, 0, 0)
        res = compress_directory(
            source_dir=self.test_dir,
            output_dir=self.output_dir,
            archive_format="tar.gz",
            custom_timestamp=fixed_time
        )
        self.assertTrue(res.archive_path.exists())
        self.assertEqual(res.archive_path.name, "backup_2026-11-17_100000.tar.gz")
        self.assertEqual(res.file_count, 2)
        self.assertTrue(tarfile.is_tarfile(res.archive_path))
        self.assertEqual(len(res.sha256_checksum), 64)

    def test_compress_zip(self):
        fixed_time = datetime(2026, 11, 17, 10, 0, 0)
        res = compress_directory(
            source_dir=self.test_dir,
            output_dir=self.output_dir,
            archive_format="zip",
            custom_timestamp=fixed_time
        )
        self.assertTrue(res.archive_path.exists())
        self.assertEqual(res.archive_path.name, "backup_2026-11-17_100000.zip")
        self.assertEqual(res.file_count, 2)
        self.assertTrue(zipfile.is_zipfile(res.archive_path))


class TestS3Upload(unittest.TestCase):
    """Tests for S3 uploader logic."""

    def setUp(self):
        self.mock_s3 = MagicMock()
        self.uploader = S3Uploader(
            bucket_name="test-bucket",
            region="us-east-1",
            s3_client=self.mock_s3
        )
        self.test_file = Path("tests/tmp_test_archive.tar.gz")
        self.test_file.write_bytes(b"dummy archive data")

    def tearDown(self):
        if self.test_file.exists():
            self.test_file.unlink()

    def test_generate_s3_key(self):
        fixed_time = datetime(2026, 11, 17, 12, 0, 0)
        key = self.uploader.generate_s3_key("backup.tar.gz", prefix="backups", timestamp=fixed_time)
        self.assertEqual(key, "backups/2026/11/17/backup.tar.gz")

    def test_upload_file_success(self):
        # Simulate object does not exist yet (head_object raises 404)
        self.mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}},
            "HeadObject"
        )

        res = self.uploader.upload_file(
            file_path=self.test_file,
            s3_key="backups/2026/11/17/test.tar.gz",
            sha256_checksum="abc123hash",
            metadata={"file-count": "2"},
            skip_duplicate=True
        )

        self.assertFalse(res.is_duplicate)
        self.assertEqual(res.bucket, "test-bucket")
        self.mock_s3.upload_file.assert_called_once()
        _, kwargs = self.mock_s3.upload_file.call_args
        self.assertEqual(kwargs["ExtraArgs"]["ServerSideEncryption"], "AES256")
        self.assertEqual(kwargs["ExtraArgs"]["Metadata"]["sha256"], "abc123hash")

    def test_upload_skip_duplicate(self):
        # Simulate object already exists with matching sha256
        self.mock_s3.head_object.return_value = {
            "Metadata": {"sha256": "abc123hash"}
        }

        res = self.uploader.upload_file(
            file_path=self.test_file,
            s3_key="backups/2026/11/17/test.tar.gz",
            sha256_checksum="abc123hash",
            skip_duplicate=True
        )

        self.assertTrue(res.is_duplicate)
        self.mock_s3.upload_file.assert_not_called()

    def test_upload_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            self.uploader.upload_file(
                file_path=Path("non_existent_file.tar.gz"),
                s3_key="key",
                sha256_checksum="123"
            )


class TestCloudWatchMonitoring(unittest.TestCase):
    """Tests for CloudWatch metrics and structured logging."""

    def setUp(self):
        self.mock_cw = MagicMock()
        self.mock_logs = MagicMock()
        self.mock_sns = MagicMock()
        self.monitor = CloudWatchMonitor(
            namespace="AWSBackupRecovery/Test",
            log_group_name="/aws/test/logs",
            region="us-east-1",
            cw_client=self.mock_cw,
            logs_client=self.mock_logs,
            sns_client=self.mock_sns,
            sns_topic_arn="arn:aws:sns:us-east-1:123456789012:test-topic"
        )

    def test_publish_metrics_success(self):
        res = self.monitor.publish_metrics(
            success=True,
            duration_seconds=5.2,
            size_bytes=1048576,
            file_count=10
        )
        self.assertTrue(res)
        self.mock_cw.put_metric_data.assert_called_once()
        call_args = self.mock_cw.put_metric_data.call_args[1]
        self.assertEqual(call_args["Namespace"], "AWSBackupRecovery/Test")
        metrics = {m["MetricName"]: m["Value"] for m in call_args["MetricData"]}
        self.assertEqual(metrics["BackupSuccess"], 1.0)
        self.assertEqual(metrics["BackupFailure"], 0.0)
        self.assertEqual(metrics["BackupSizeBytes"], 1048576.0)

    def test_publish_metrics_failure(self):
        res = self.monitor.publish_metrics(
            success=False,
            duration_seconds=1.1
        )
        self.assertTrue(res)
        call_args = self.mock_cw.put_metric_data.call_args[1]
        metrics = {m["MetricName"]: m["Value"] for m in call_args["MetricData"]}
        self.assertEqual(metrics["BackupSuccess"], 0.0)
        self.assertEqual(metrics["BackupFailure"], 1.0)

    def test_send_log_event(self):
        res = self.monitor.send_log_event({"status": "SUCCESS", "bytes": 100})
        self.assertTrue(res)
        self.mock_logs.put_log_events.assert_called_once()

    def test_send_sns_alert(self):
        res = self.monitor.send_sns_alert("Subject", "Body error")
        self.assertTrue(res)
        self.mock_sns.publish.assert_called_once_with(
            TopicArn="arn:aws:sns:us-east-1:123456789012:test-topic",
            Subject="Subject",
            Message="Body error"
        )


class TestEndToEndBackup(unittest.TestCase):
    """End-to-end backup pipeline integration test with mocked AWS clients."""

    def setUp(self):
        self.source_dir = Path("tests/tmp_e2e_source")
        self.source_dir.mkdir(parents=True, exist_ok=True)
        (self.source_dir / "sample.txt").write_text("E2E Test File Content", encoding="utf-8")

    def tearDown(self):
        import shutil
        if self.source_dir.exists():
            shutil.rmtree(self.source_dir, ignore_errors=True)

    @patch("backup.backup.S3Uploader")
    @patch("backup.backup.CloudWatchMonitor")
    def test_run_backup_success(self, mock_cw_cls, mock_uploader_cls):
        mock_uploader = MagicMock()
        mock_uploader_cls.return_value = mock_uploader
        mock_uploader.generate_s3_key.return_value = "backups/2026/11/17/backup.tar.gz"
        mock_uploader.upload_file.return_value = UploadResult(
            s3_uri="s3://test-bucket/backups/2026/11/17/backup.tar.gz",
            bucket="test-bucket",
            key="backups/2026/11/17/backup.tar.gz",
            size_bytes=500,
            sha256_checksum="sha256test",
            upload_time_seconds=0.5,
            is_duplicate=False
        )

        mock_cw = MagicMock()
        mock_cw_cls.return_value = mock_cw

        config = BackupConfig(
            s3_bucket_name="test-bucket",
            backup_source_dir=str(self.source_dir),
            backup_format="tar.gz"
        )

        success = run_backup(
            config=config,
            staging_dir="tests/tmp_e2e_staging",
            keep_local=False
        )

        self.assertTrue(success)
        mock_uploader.upload_file.assert_called_once()
        mock_cw.publish_metrics.assert_called_once()

    def test_run_backup_missing_source_fails(self):
        config = BackupConfig(
            s3_bucket_name="test-bucket",
            backup_source_dir="tests/non_existent_folder_xyz"
        )
        success = run_backup(config=config)
        self.assertFalse(success)


if __name__ == "__main__":
    unittest.main()

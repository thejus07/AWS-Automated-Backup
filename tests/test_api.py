"""
Unit tests for Vercel Serverless Function API & Management Dashboard.
Mocks AWS S3 calls to ensure fast, local, zero-cost execution.
"""

from datetime import datetime
import json
import unittest
from unittest.mock import MagicMock, patch

from api.index import app


class TestVercelDashboardAPI(unittest.TestCase):
    """Tests for the Flask/Vercel serverless application."""

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_dashboard_renders(self):
        """Tests that the main dashboard UI renders successfully."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("AWS Backup & Recovery", html)
        self.assertIn("Vercel Serverless", html)

    @patch("api.index.os.getenv")
    def test_api_health_unconfigured(self, mock_getenv):
        """Tests health check when S3_BUCKET_NAME is not set."""
        mock_getenv.side_effect = lambda k, default=None: "" if k == "S3_BUCKET_NAME" else default
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "UNCONFIGURED")

    @patch("api.index.get_s3_client")
    @patch("api.index.os.getenv")
    def test_api_health_healthy(self, mock_getenv, mock_s3_getter):
        """Tests health check when S3 bucket is reachable."""
        mock_getenv.side_effect = lambda k, default=None: "test-bucket" if k == "S3_BUCKET_NAME" else default
        mock_s3 = MagicMock()
        mock_s3_getter.return_value = mock_s3

        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "HEALTHY")
        self.assertEqual(data["bucket"], "test-bucket")

    @patch("api.index.get_s3_client")
    @patch("api.index.os.getenv")
    def test_api_backups_list(self, mock_getenv, mock_s3_getter):
        """Tests listing backups via the /api/backups endpoint."""
        mock_getenv.side_effect = lambda k, default=None: {
            "S3_BUCKET_NAME": "test-bucket",
            "S3_BACKUP_PREFIX": "backups"
        }.get(k, default)

        mock_s3 = MagicMock()
        mock_s3_getter.return_value = mock_s3

        paginator = MagicMock()
        mock_s3.get_paginator.return_value = paginator
        paginator.paginate.return_value = [
            {
                "Contents": [
                    {
                        "Key": "backups/2026/11/17/backup_2026-11-17_100000.tar.gz",
                        "Size": 1048576,
                        "LastModified": datetime(2026, 11, 17, 10, 0, 0),
                    }
                ]
            }
        ]

        mock_s3.head_object.return_value = {
            "Metadata": {"sha256": "testhash123", "file-count": "15"}
        }

        response = self.client.get("/api/backups")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["backups"][0]["key"], "backups/2026/11/17/backup_2026-11-17_100000.tar.gz")
        self.assertEqual(data["backups"][0]["size_readable"], "1.00 MB")
        self.assertEqual(data["backups"][0]["sha256"], "testhash123")
        self.assertEqual(data["backups"][0]["file_count"], 15)

    @patch("api.index.get_s3_client")
    @patch("api.index.os.getenv")
    def test_api_download_presigned_url(self, mock_getenv, mock_s3_getter):
        """Tests presigned URL generation for secure direct download."""
        mock_getenv.side_effect = lambda k, default=None: "test-bucket" if k == "S3_BUCKET_NAME" else default
        mock_s3 = MagicMock()
        mock_s3_getter.return_value = mock_s3
        mock_s3.generate_presigned_url.return_value = "https://s3.amazonaws.com/test-bucket/backups/backup.tar.gz?signature=xyz"

        # Request with JSON Accept header
        response = self.client.get(
            "/api/download?key=backups/backup.tar.gz",
            headers={"Accept": "application/json"}
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("download_url", data)
        self.assertEqual(data["expires_in"], 900)

    def test_api_download_missing_key(self):
        """Tests that /api/download rejects requests without key parameter."""
        response = self.client.get("/api/download")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()

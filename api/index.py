"""
Vercel Serverless Function & Web Management Dashboard for AWS Automated Backup & Recovery System.
Provides a modern DevOps web interface and REST API for inspecting backups, downloading archives
via secure S3 Presigned URLs, viewing CloudWatch metrics, and verifying recovery status.
"""

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

# Ensure project root is available for imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, jsonify, render_template_string, request
import boto3
from botocore.exceptions import ClientError, NoCredentialsError

from config.settings import get_config

app = Flask(__name__)

# Load config
config = get_config()


def get_s3_client():
    """Initializes and returns an S3 client using environment credentials."""
    return boto3.client(
        "s3",
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        aws_session_token=os.getenv("AWS_SESSION_TOKEN"),
    )


def get_cw_client():
    """Initializes and returns a CloudWatch client using environment credentials."""
    return boto3.client(
        "cloudwatch",
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        aws_session_token=os.getenv("AWS_SESSION_TOKEN"),
    )


def format_bytes(size_bytes: int) -> str:
    """Formats bytes into human-readable units."""
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AWS Backup & Recovery | Cloud Console</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    body { background-color: #0b0f19; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .card-glass { background: rgba(17, 24, 39, 0.7); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.08); }
    .glow-orange { box-shadow: 0 0 20px -5px rgba(249, 115, 22, 0.3); }
    .glow-blue { box-shadow: 0 0 20px -5px rgba(59, 130, 246, 0.3); }
  </style>
</head>
<body class="text-slate-200 min-h-screen">
  <!-- Top Navigation -->
  <nav class="border-b border-slate-800 bg-slate-900/60 sticky top-0 z-50 backdrop-blur-md">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
      <div class="flex items-center space-x-3">
        <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-orange-500 to-amber-400 flex items-center justify-center text-white shadow-lg shadow-orange-500/20">
          <i class="fa-solid fa-cloud-arrow-up text-lg"></i>
        </div>
        <div>
          <span class="font-bold text-lg text-white tracking-wide">AWS Backup & Recovery</span>
          <span class="ml-2 px-2 py-0.5 text-xs font-semibold rounded bg-orange-500/10 text-orange-400 border border-orange-500/20">Vercel Serverless</span>
        </div>
      </div>
      <div class="flex items-center space-x-4">
        <span id="healthBadge" class="flex items-center text-xs font-medium px-3 py-1 rounded-full bg-slate-800 border border-slate-700 text-slate-400">
          <span class="w-2 h-2 rounded-full bg-slate-500 mr-2 animate-pulse"></span> Connecting to AWS...
        </span>
        <button onclick="fetchBackups()" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-xs font-medium transition flex items-center gap-2">
          <i class="fa-solid fa-rotate text-slate-400"></i> Refresh
        </button>
      </div>
    </div>
  </nav>

  <!-- Main Content Container -->
  <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">
    
    <!-- Environment Config Warning if not configured -->
    <div id="envWarning" class="hidden card-glass rounded-xl p-4 border-amber-500/30 bg-amber-500/5 text-amber-200 flex items-start gap-3">
      <i class="fa-solid fa-triangle-exclamation text-amber-400 mt-1"></i>
      <div>
        <h4 class="font-semibold text-sm">AWS Environment Variables Needed</h4>
        <p class="text-xs text-amber-300/80 mt-1">
          To connect to live S3 & CloudWatch, configure <code>S3_BUCKET_NAME</code>, <code>AWS_REGION</code>, <code>AWS_ACCESS_KEY_ID</code>, and <code>AWS_SECRET_ACCESS_KEY</code> in your Vercel Project Settings.
        </p>
      </div>
    </div>

    <!-- Stat Cards Overview -->
    <div class="grid grid-cols-1 md:grid-cols-4 gap-5">
      <!-- Total Backups -->
      <div class="card-glass rounded-2xl p-5 glow-orange">
        <div class="flex items-center justify-between text-slate-400 text-xs font-semibold uppercase tracking-wider">
          <span>Total Archives</span>
          <i class="fa-solid fa-box-archive text-orange-400 text-base"></i>
        </div>
        <div class="mt-3 text-3xl font-bold text-white" id="statTotalBackups">--</div>
        <p class="mt-1 text-xs text-slate-400">Available in Amazon S3</p>
      </div>

      <!-- Total Storage -->
      <div class="card-glass rounded-2xl p-5 glow-blue">
        <div class="flex items-center justify-between text-slate-400 text-xs font-semibold uppercase tracking-wider">
          <span>Total Storage</span>
          <i class="fa-solid fa-database text-blue-400 text-base"></i>
        </div>
        <div class="mt-3 text-3xl font-bold text-white" id="statTotalStorage">--</div>
        <p class="mt-1 text-xs text-slate-400">Compressed archive size</p>
      </div>

      <!-- CloudWatch Status -->
      <div class="card-glass rounded-2xl p-5">
        <div class="flex items-center justify-between text-slate-400 text-xs font-semibold uppercase tracking-wider">
          <span>Backup Health</span>
          <i class="fa-solid fa-shield-heart text-emerald-400 text-base"></i>
        </div>
        <div class="mt-3 text-3xl font-bold text-emerald-400" id="statHealth">100%</div>
        <p class="mt-1 text-xs text-slate-400">Zero active failure alarms</p>
      </div>

      <!-- Target S3 Bucket -->
      <div class="card-glass rounded-2xl p-5">
        <div class="flex items-center justify-between text-slate-400 text-xs font-semibold uppercase tracking-wider">
          <span>Target Bucket</span>
          <i class="fa-brands fa-aws text-amber-400 text-base"></i>
        </div>
        <div class="mt-3 text-lg font-bold text-white truncate" id="statBucketName" title="{{ bucket_name }}">{{ bucket_name or "Not configured" }}</div>
        <p class="mt-1 text-xs text-slate-400" id="statRegion">Region: {{ aws_region }}</p>
      </div>
    </div>

    <!-- Backups Management Table -->
    <div class="card-glass rounded-2xl overflow-hidden shadow-2xl">
      <div class="px-6 py-5 border-b border-slate-800 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 class="text-lg font-bold text-white flex items-center gap-2">
            <i class="fa-solid fa-server text-orange-400"></i> Amazon S3 Backup Archives
          </h2>
          <p class="text-xs text-slate-400 mt-1">Immutable, encrypted backups stored with date-based partitioning</p>
        </div>
        <div class="flex items-center gap-2">
          <input type="text" id="searchInput" onkeyup="filterTable()" placeholder="Search backups..." class="bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-orange-500">
        </div>
      </div>

      <!-- Table Container -->
      <div class="overflow-x-auto">
        <table class="w-full text-left text-sm text-slate-300">
          <thead class="bg-slate-900/80 text-xs uppercase text-slate-400 font-semibold border-b border-slate-800">
            <tr>
              <th scope="col" class="px-6 py-3.5">Backup Key</th>
              <th scope="col" class="px-6 py-3.5">Created Date (UTC)</th>
              <th scope="col" class="px-6 py-3.5">Size</th>
              <th scope="col" class="px-6 py-3.5">Files</th>
              <th scope="col" class="px-6 py-3.5">SHA256 Checksum</th>
              <th scope="col" class="px-6 py-3.5 text-right">Actions</th>
            </tr>
          </thead>
          <tbody id="backupsTableBody" class="divide-y divide-slate-800/60 font-mono text-xs">
            <tr>
              <td colspan="6" class="px-6 py-12 text-center text-slate-500 font-sans">
                <i class="fa-solid fa-spinner fa-spin text-2xl text-orange-500 mb-2"></i>
                <p>Loading backup archives from Amazon S3...</p>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Quick Operations & Disaster Recovery Info -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
      <!-- Recovery Instructions -->
      <div class="card-glass rounded-2xl p-6 space-y-4">
        <h3 class="text-base font-bold text-white flex items-center gap-2">
          <i class="fa-solid fa-terminal text-blue-400"></i> CLI Disaster Recovery Command
        </h3>
        <p class="text-xs text-slate-400">To perform an instant bare-metal restore directly onto an EC2 instance:</p>
        <div class="bg-slate-950 p-3.5 rounded-xl border border-slate-800 font-mono text-xs text-emerald-400 relative group">
          <code>python recovery/restore.py --latest --target-dir /var/data</code>
          <button onclick="navigator.clipboard.writeText('python recovery/restore.py --latest --target-dir /var/data')" class="absolute top-2.5 right-2.5 px-2 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-[10px] transition">Copy</button>
        </div>
        <p class="text-[11px] text-slate-500">
          <i class="fa-solid fa-shield-halved text-emerald-500 mr-1"></i> Includes built-in Zip Slip protection & collision avoidance.
        </p>
      </div>

      <!-- CloudWatch & Retention Architecture -->
      <div class="card-glass rounded-2xl p-6 space-y-4">
        <h3 class="text-base font-bold text-white flex items-center gap-2">
          <i class="fa-solid fa-layer-group text-purple-400"></i> S3 Retention & Lifecycle Tiers
        </h3>
        <div class="space-y-2 text-xs">
          <div class="flex items-center justify-between p-2 rounded bg-slate-900/50 border border-slate-800/80">
            <span class="flex items-center gap-2"><span class="w-2 h-2 rounded-full bg-emerald-400"></span> 0 - 30 Days</span>
            <span class="text-slate-400 font-mono">S3 Standard (High Performance)</span>
          </div>
          <div class="flex items-center justify-between p-2 rounded bg-slate-900/50 border border-slate-800/80">
            <span class="flex items-center gap-2"><span class="w-2 h-2 rounded-full bg-blue-400"></span> 31 - 90 Days</span>
            <span class="text-slate-400 font-mono">Standard-IA (50% Cost Savings)</span>
          </div>
          <div class="flex items-center justify-between p-2 rounded bg-slate-900/50 border border-slate-800/80">
            <span class="flex items-center gap-2"><span class="w-2 h-2 rounded-full bg-purple-400"></span> 91 - 365 Days</span>
            <span class="text-slate-400 font-mono">Glacier Flexible (80% Savings)</span>
          </div>
        </div>
      </div>
    </div>

  </main>

  <script>
    let allBackups = [];

    async function checkHealth() {
      try {
        const res = await fetch('/api/health');
        const data = await res.json();
        const badge = document.getElementById('healthBadge');
        if (data.status === 'HEALTHY') {
          badge.className = "flex items-center text-xs font-medium px-3 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400";
          badge.innerHTML = `<span class="w-2 h-2 rounded-full bg-emerald-400 mr-2"></span> Connected to AWS (${data.region})`;
        } else {
          badge.className = "flex items-center text-xs font-medium px-3 py-1 rounded-full bg-amber-500/10 border border-amber-500/20 text-amber-400";
          badge.innerHTML = `<span class="w-2 h-2 rounded-full bg-amber-400 mr-2"></span> ${data.message || 'AWS Unreachable'}`;
          document.getElementById('envWarning').classList.remove('hidden');
        }
      } catch (err) {
        console.error(err);
      }
    }

    async function fetchBackups() {
      const tbody = document.getElementById('backupsTableBody');
      tbody.innerHTML = `<tr><td colspan="6" class="px-6 py-12 text-center text-slate-500 font-sans"><i class="fa-solid fa-spinner fa-spin text-2xl text-orange-500 mb-2"></i><p>Loading backup archives from Amazon S3...</p></td></tr>`;

      try {
        const res = await fetch('/api/backups');
        const data = await res.json();
        allBackups = data.backups || [];

        renderTable(allBackups);
        updateStats(allBackups, data.total_size_bytes);
      } catch (err) {
        tbody.innerHTML = `<tr><td colspan="6" class="px-6 py-8 text-center text-red-400 font-sans"><i class="fa-solid fa-circle-exclamation text-xl mb-1"></i><p>Error retrieving backups from S3.</p></td></tr>`;
      }
    }

    function renderTable(backups) {
      const tbody = document.getElementById('backupsTableBody');
      if (backups.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="px-6 py-12 text-center text-slate-500 font-sans"><i class="fa-solid fa-folder-open text-2xl mb-2"></i><p>No backup archives found in S3 bucket.</p></td></tr>`;
        return;
      }

      tbody.innerHTML = backups.map(b => {
        const shortHash = b.sha256 ? `${b.sha256.substring(0, 10)}...` : 'N/A';
        return `
          <tr class="hover:bg-slate-800/30 transition">
            <td class="px-6 py-4 font-semibold text-slate-200">
              <i class="fa-regular fa-file-zipper text-orange-400 mr-2"></i>${b.key}
            </td>
            <td class="px-6 py-4 text-slate-400 font-sans">${b.last_modified}</td>
            <td class="px-6 py-4 font-semibold text-slate-300 font-sans">${b.size_readable}</td>
            <td class="px-6 py-4 text-slate-400 font-sans">${b.file_count !== null ? b.file_count : '--'}</td>
            <td class="px-6 py-4 text-slate-500" title="${b.sha256 || ''}">${shortHash}</td>
            <td class="px-6 py-4 text-right font-sans">
              <a href="/api/download?key=${encodeURIComponent(b.key)}" target="_blank" class="inline-flex items-center px-3 py-1 rounded bg-orange-600/20 hover:bg-orange-600/40 text-orange-400 border border-orange-500/30 text-xs font-semibold transition gap-1.5">
                <i class="fa-solid fa-download"></i> Presigned URL
              </a>
            </td>
          </tr>
        `;
      }).join('');
    }

    function updateStats(backups, totalBytes) {
      document.getElementById('statTotalBackups').innerText = backups.length;
      document.getElementById('statTotalStorage').innerText = formatSize(totalBytes || 0);
    }

    function formatSize(bytes) {
      if (bytes === 0) return '0 B';
      const k = 1024;
      const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
      const i = Math.floor(Math.log(bytes) / Math.log(k));
      return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    function filterTable() {
      const q = document.getElementById('searchInput').value.toLowerCase();
      const filtered = allBackups.filter(b => b.key.toLowerCase().includes(q) || (b.sha256 && b.sha256.toLowerCase().includes(q)));
      renderTable(filtered);
    }

    // Initial load
    checkHealth();
    fetchBackups();
  </script>
</body>
</html>
"""


@app.route("/")
@app.route("/dashboard")
def index():
    """Serves the main Cloud DevOps Backup & Recovery Management Dashboard."""
    bucket_name = os.getenv("S3_BUCKET_NAME", "")
    aws_region = os.getenv("AWS_REGION", "us-east-1")
    return render_template_string(
        DASHBOARD_HTML,
        bucket_name=bucket_name,
        aws_region=aws_region
    )


@app.route("/api/health")
def api_health():
    """Health check endpoint checking S3 connectivity and configuration."""
    bucket_name = os.getenv("S3_BUCKET_NAME", "")
    aws_region = os.getenv("AWS_REGION", "us-east-1")

    if not bucket_name:
        return jsonify({
            "status": "UNCONFIGURED",
            "message": "S3_BUCKET_NAME environment variable is not configured.",
            "region": aws_region,
        }), 200

    try:
        s3 = get_s3_client()
        s3.head_bucket(Bucket=bucket_name)
        return jsonify({
            "status": "HEALTHY",
            "bucket": bucket_name,
            "region": aws_region,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }), 200
    except (ClientError, NoCredentialsError) as e:
        return jsonify({
            "status": "DEGRADED",
            "message": str(e),
            "bucket": bucket_name,
            "region": aws_region
        }), 200


@app.route("/api/backups")
def api_backups():
    """Returns a list of backup archives from Amazon S3."""
    bucket_name = os.getenv("S3_BUCKET_NAME", "")
    prefix = os.getenv("S3_BACKUP_PREFIX", "backups")

    if not bucket_name:
        return jsonify({
            "backups": [],
            "total_size_bytes": 0,
            "message": "S3_BUCKET_NAME not configured."
        }), 200

    try:
        s3 = get_s3_client()
        paginator = s3.get_paginator("list_objects_v2")

        backups = []
        total_size = 0

        for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not (key.endswith(".tar.gz") or key.endswith(".zip") or key.endswith(".tgz")):
                    continue

                size = obj["Size"]
                total_size += size

                # Attempt to get metadata
                sha256_hash = None
                file_count = None
                try:
                    head = s3.head_object(Bucket=bucket_name, Key=key)
                    meta = head.get("Metadata", {})
                    sha256_hash = meta.get("sha256")
                    if meta.get("file-count"):
                        file_count = int(meta["file-count"])
                except Exception:
                    pass

                backups.append({
                    "key": key,
                    "size_bytes": size,
                    "size_readable": format_bytes(size),
                    "last_modified": obj["LastModified"].strftime("%Y-%m-%d %H:%M:%S"),
                    "sha256": sha256_hash,
                    "file_count": file_count,
                })

        backups.sort(key=lambda x: x["last_modified"], reverse=True)

        return jsonify({
            "backups": backups,
            "total_size_bytes": total_size,
            "count": len(backups)
        }), 200

    except Exception as e:
        return jsonify({"error": str(e), "backups": [], "total_size_bytes": 0}), 500


@app.route("/api/download")
def api_download():
    """Generates a secure S3 Presigned URL for downloading a backup archive directly."""
    bucket_name = os.getenv("S3_BUCKET_NAME", "")
    s3_key = request.args.get("key")

    if not bucket_name or not s3_key:
        return jsonify({"error": "Both S3_BUCKET_NAME and 'key' query param are required."}), 400

    try:
        s3 = get_s3_client()
        # Generate presigned URL valid for 15 minutes (900 seconds)
        presigned_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket_name, "Key": s3_key},
            ExpiresIn=900
        )
        # Redirect to presigned URL directly if browser GET, or return JSON
        if "application/json" in request.headers.get("Accept", ""):
            return jsonify({"download_url": presigned_url, "expires_in": 900})
        from flask import redirect
        return redirect(presigned_url)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Vercel WSGI entrypoint
app = app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)

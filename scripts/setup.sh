#!/usr/bin/env bash
# ==============================================================================
# Script: setup.sh
# Purpose: Prepares directory structure, environment variables, permissions,
#          and cron scheduling for automated backups on Linux EC2.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "============================================================"
echo "Configuring AWS Automated Backup & Recovery System"
echo "Project Root: ${PROJECT_ROOT}"
echo "============================================================"

cd "$PROJECT_ROOT"

# 1. Create runtime directories
echo "[*] Creating runtime directories..."
mkdir -p logs backup_staging restore_staging

# 2. Setup environment configuration
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        echo "[*] Initializing .env from .env.example..."
        cp .env.example .env
        echo "[!] Remember to edit .env and configure S3_BUCKET_NAME and AWS_REGION."
    fi
else
    echo "[*] Found existing .env file."
fi

# 3. Create sample backup directory if default does not exist
DEFAULT_BACKUP_DIR="/var/data"
if [ ! -d "$DEFAULT_BACKUP_DIR" ]; then
    echo "[*] Creating default backup directory: ${DEFAULT_BACKUP_DIR}..."
    if sudo mkdir -p "$DEFAULT_BACKUP_DIR" 2>/dev/null; then
        sudo chown -R "$(whoami)":"$(whoami)" "$DEFAULT_BACKUP_DIR"
        echo "Sample data generated on $(date)" > "${DEFAULT_BACKUP_DIR}/sample.txt"
        echo "Created sample data at ${DEFAULT_BACKUP_DIR}/sample.txt"
    else
        echo "[!] Could not create /var/data (insufficient privileges). Using ./sample_data fallback."
        mkdir -p "${PROJECT_ROOT}/sample_data"
        echo "Sample data generated on $(date)" > "${PROJECT_ROOT}/sample_data/sample.txt"
    fi
fi

# 4. Set executable permissions on scripts
echo "[*] Granting executable permissions..."
chmod +x scripts/*.sh backup/backup.py recovery/restore.py

# 5. Optional Cron scheduling setup
setup_cron() {
    local schedule="${1:-daily}"
    local cron_time
    if [ "$schedule" == "weekly" ]; then
        # Every Sunday at 02:00 AM
        cron_time="0 2 * * 0"
    else
        # Daily at 02:00 AM
        cron_time="0 2 * * *"
    fi

    local python_bin="${PROJECT_ROOT}/venv/bin/python"
    if [ ! -f "$python_bin" ]; then
        python_bin="$(command -v python3)"
    fi

    local backup_cmd="${python_bin} ${PROJECT_ROOT}/backup/backup.py >> ${PROJECT_ROOT}/logs/cron_backup.log 2>&1"
    local cron_entry="${cron_time} ${backup_cmd} # AWS-AUTOMATED-BACKUP"

    # Avoid duplicate cron entries
    crontab -l 2>/dev/null | grep -v "AWS-AUTOMATED-BACKUP" | { cat; echo "$cron_entry"; } | crontab -
    echo "[*] Scheduled backup job in crontab (${schedule} schedule: '${cron_time}')"
}

if [ "${1:-}" == "--cron" ] || [ "${1:-}" == "-c" ]; then
    SCHEDULE_TYPE="${2:-daily}"
    setup_cron "$SCHEDULE_TYPE"
else
    echo ""
    echo "Tip: To automatically install a cron schedule, run:"
    echo "  ./scripts/setup.sh --cron daily    # (Runs every day at 02:00 AM)"
    echo "  ./scripts/setup.sh --cron weekly   # (Runs every Sunday at 02:00 AM)"
fi

echo "============================================================"
echo "[SUCCESS] Environment setup complete!"
echo "============================================================"

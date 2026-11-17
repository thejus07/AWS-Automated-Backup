#!/usr/bin/env bash
# ==============================================================================
# Script: install_dependencies.sh
# Purpose: Installs Python 3, pip, virtual environment tools, and required
#          packages on EC2 (Amazon Linux 2023, Amazon Linux 2, Ubuntu, Debian).
# ==============================================================================

set -euo pipefail

echo "============================================================"
echo "Installing System Dependencies for AWS Backup & Recovery"
echo "============================================================"

# Detect package manager
if command -v dnf &> /dev/null; then
    echo "[*] Detected dnf (Amazon Linux 2023 / RHEL 9 / Fedora)..."
    sudo dnf update -y
    sudo dnf install -y python3 python3-pip tar gzip awscli
elif command -v yum &> /dev/null; then
    echo "[*] Detected yum (Amazon Linux 2 / CentOS / RHEL 7/8)..."
    sudo yum update -y
    sudo yum install -y python3 python3-pip tar gzip awscli
elif command -v apt-get &> /dev/null; then
    echo "[*] Detected apt (Ubuntu / Debian)..."
    sudo apt-get update -y
    sudo apt-get install -y python3 python3-pip python3-venv tar gzip awscli
else
    echo "[!] Unrecognized package manager. Please install Python 3 and pip manually."
fi

# Set up Python virtual environment in project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "[*] Setting up Python virtual environment at ${PROJECT_ROOT}/venv..."
cd "$PROJECT_ROOT"

if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

# Activate virtual environment
# shellcheck source=/dev/null
source venv/bin/activate

# Upgrade pip and install requirements
echo "[*] Installing Python packages from requirements.txt..."
pip install --upgrade pip
pip install -r requirements.txt

echo "============================================================"
echo "[SUCCESS] Dependencies installed successfully!"
echo "To activate the environment: source ${PROJECT_ROOT}/venv/bin/activate"
echo "============================================================"

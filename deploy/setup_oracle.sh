#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# JobHunt Oracle Cloud Infrastructure (OCI) Automated Setup Script
# Works on Ubuntu 22.04 / 24.04 LTS (AMD64 & ARM64 / Ampere A1)
# ==============================================================================

echo "======================================================"
echo "🚀 Starting JobHunt Setup on Oracle Cloud VM..."
echo "======================================================"

# 1. Update system packages
echo "📦 Updating apt packages..."
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv git curl iptables-persistent

# 2. CRITICAL ORACLE CLOUD STEP: Open VM OS-level firewall
# By default, Oracle Ubuntu instances reject all incoming traffic on ports other than 22.
echo "🛡️ Configuring Oracle Cloud OS Firewall (iptables)..."
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8000 -j ACCEPT || sudo iptables -I INPUT 1 -p tcp --dport 8000 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT || true
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT || true
sudo netfilter-persistent save

# 3. Setup Python Virtual Environment
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

echo "🐍 Setting up Python Virtual Environment in $APP_DIR..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Ensure log directory exists
mkdir -p out

# 4. Check for .env file
if [ ! -f ".env" ]; then
    echo "⚠️  No .env file found!"
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "Created .env from .env.example. Please edit it with your real credentials: nano .env"
    fi
fi

# 5. Install and enable systemd service
echo "⚙️ Setting up systemd background service..."
SERVICE_FILE="/etc/systemd/system/jobhunt.service"

sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=JobHunt FastAPI Bridge Server for Dify.ai
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/uvicorn bridge_server:app --host 0.0.0.0 --port 8000 --workers 2
Restart=always
RestartSec=5
EnvironmentFile=$APP_DIR/.env
StandardOutput=append:$APP_DIR/out/bridge.log
StandardError=append:$APP_DIR/out/bridge.err

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable jobhunt.service
sudo systemctl restart jobhunt.service

echo ""
echo "======================================================"
echo "✅ JobHunt Service is successfully installed and running!"
echo "======================================================"
sudo systemctl status jobhunt.service --no-pager || true

echo ""
echo "📍 Test locally:"
echo "   curl http://127.0.0.1:8000/health"
echo ""
echo "🌐 Public Endpoint:"
echo "   http://$(curl -s ifconfig.me):8000/health"
echo ""
echo "⚠️  REMINDER FOR ORACLE CLOUD WEB CONSOLE:"
echo "   Make sure to add an Ingress Rule in your OCI VCN Security List:"
echo "   - Source CIDR: 0.0.0.0/0"
echo "   - IP Protocol: TCP"
echo "   - Destination Port Range: 8000 (and 80, 443 if using SSL)"
echo "======================================================"

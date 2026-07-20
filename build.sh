#!/bin/bash
# BUILD phase only: install deps + Playwright browsers.
set -e

export MISE_PYTHON_GITHUB_ATTRIBUTIONS=false

echo "[build] Installing Python dependencies..."
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

echo "[build] Installing system packages (tor)..."
if command -v apt-get >/dev/null 2>&1; then
    apt-get update || true
    apt-get install -y tor || true
fi

echo "[build] Installing Playwright Chromium browser + system deps..."
python3 -m playwright install --with-deps chromium || python3 -m playwright install chromium

echo "[build] Done."

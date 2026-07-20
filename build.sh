#!/bin/bash
# BUILD phase only: install deps + Playwright browsers.
# Do NOT start the server here (that belongs in start.sh / the run phase),
# otherwise the build step "completes" only after the server is killed,
# leaving nothing listening for the platform's health check.
set -e

export MISE_PYTHON_GITHUB_ATTESTATIONS=false

echo "[build] Installing Python dependencies..."
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

echo "[build] Installing Playwright Chromium browser..."
python3 -m playwright install --with-deps chromium || python3 -m playwright install chromium

echo "[build] Done."

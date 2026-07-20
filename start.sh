#!/bin/bash
set -e

# mise 2026 enforces GitHub attestation checks for Python builds that aren't
# published with attestations. Disable it so `mise install` can proceed.
export MISE_PYTHON_GITHUB_ATTESTATIONS=false

echo "[start] Installing Python dependencies..."
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

echo "[start] Installing Playwright Chromium browser..."
python3 -m playwright install --with-deps chromium || python3 -m playwright install chromium

echo "[start] Starting app on port ${PORT:-5000}..."
exec python3 app.py

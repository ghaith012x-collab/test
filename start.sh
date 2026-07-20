#!/bin/bash
# RUN phase: start the web server. The platform health-checks this process.
set -e

export MISE_PYTHON_GITHUB_ATTESTATIONS=false

PORT="${PORT:-5000}"
echo "[start] Starting app on port ${PORT}..."
exec python3 app.py

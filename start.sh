#!/bin/bash
# RUN phase: start the web server. The platform health-checks this process.
set -e

export MISE_PYTHON_GITHUB_ATTESTATIONS=false

PORT="${PORT:-5000}"
echo "[start] Starting app on port ${PORT}..."

if command -v gunicorn >/dev/null 2>&1; then
    exec gunicorn \
        --bind "0.0.0.0:${PORT}" \
        --workers 1 \
        --threads 8 \
        --timeout 120 \
        --access-logfile - \
        --error-logfile - \
        --pid /tmp/gunicorn.pid \
        app:app
else
    exec python3 app.py
fi

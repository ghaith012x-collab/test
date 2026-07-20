#!/bin/bash
# RUN phase: start the web server. The platform health-checks this process.
export MISE_PYTHON_GITHUB_ATTESTATIONS=false

PORT="${PORT:-5000}"
echo "[start] Starting app on port ${PORT}..."

# Clear any stale gunicorn pid file from a previous run/restart.
rm -f /tmp/gunicorn.pid

if command -v gunicorn >/dev/null 2>&1; then
    exec gunicorn \
        --bind "0.0.0.0:${PORT}" \
        --workers 1 \
        --threads 8 \
        --timeout 120 \
        --graceful-timeout 30 \
        --reuse-port \
        --access-logfile - \
        --error-logfile - \
        app:app
else
    exec python3 app.py
fi

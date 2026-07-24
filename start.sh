#!/bin/sh
set -e

echo "Starting Python scraper API on :5000..."
python3 /app/python-app/app.py &
PY_PID=$!

echo "Starting n8n on :${N8N_PORT:-7860}..."
n8n start &
N8N_PID=$!

# If either process dies, kill the other and exit so the container restarts
# cleanly instead of limping along with half a stack.
wait -n "$PY_PID" "$N8N_PID"
EXIT_CODE=$?
kill "$PY_PID" "$N8N_PID" 2>/dev/null || true
exit "$EXIT_CODE"

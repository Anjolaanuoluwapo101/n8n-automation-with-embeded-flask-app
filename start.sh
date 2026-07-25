#!/bin/sh
set -e

# Start Flask API in background
python3 /app/app.py &
FLASK_PID=$!

# Start n8n in background
n8n start &
N8N_PID=$!

# If either dies, kill both and exit so HF restarts the container
wait -n
kill $FLASK_PID $N8N_PID 2>/dev/null
exit 1
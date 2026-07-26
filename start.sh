#!/bin/sh

# Start n8n first — Render must detect port 7860 as primary
n8n start &
N8N_PID=$!

# Give n8n time to bind its port before Flask starts
sleep 5

# Start Flask API
python3 /app/app.py &
FLASK_PID=$!

# If either dies, kill both so Render restarts the container
wait -n
kill $N8N_PID $FLASK_PID 2>/dev/null
exit 1
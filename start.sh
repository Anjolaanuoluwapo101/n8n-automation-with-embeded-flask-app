#!/bin/sh

echo "[INIT] Creating runtime buffer directories in /tmp..."
mkdir -p /tmp/client_body /tmp/proxy /tmp/fastcgi /tmp/uwsgi /tmp/scgi

echo "[INIT] Starting nginx reverse proxy on port 7860..."
nginx -g 'daemon off;' &
NGINX_PID=$!

echo "[INIT] Starting n8n on internal port 5678..."
# Unset PORT to prevent n8n from reading Render's PORT=7860; use N8N_PORT=5678 only
unset PORT
N8N_PORT=5678 n8n start &
N8N_PID=$!

echo "[INIT] Starting Flask API on internal port 5000..."
python3 /app/app.py &
FLASK_PID=$!

echo "[INIT] All processes launched: nginx($NGINX_PID) n8n($N8N_PID) flask($FLASK_PID)"

# Monitor all 3 background processes
while kill -0 "$NGINX_PID" 2>/dev/null && kill -0 "$N8N_PID" 2>/dev/null && kill -0 "$FLASK_PID" 2>/dev/null; do
    sleep 2
done

echo "[FATAL] A process terminated unexpectedly. Shutting down container."
kill $NGINX_PID $N8N_PID $FLASK_PID 2>/dev/null
exit 1

#!/bin/sh

# ── 1. nginx (reverse proxy on :7860) ──────────────────────────────────────
# nginx owns the public port. Routes GET /health → Flask:5000,
# everything else (n8n UI + API + WS) → n8n:5678.
nginx -g 'daemon off;' &
NGINX_PID=$!

# ── 2. n8n (internal :5678) ────────────────────────────────────────────────
# Give nginx a moment to bind before n8n starts.
sleep 2
n8n start &
N8N_PID=$!

# ── 3. Flask API (internal :5000) ──────────────────────────────────────────
# Wait for n8n to finish initialising before Flask opens.
sleep 10
python3 /app/app.py &
FLASK_PID=$!

# ── If any process exits, kill the others so Render restarts the container ──
wait -n
kill $NGINX_PID $N8N_PID $FLASK_PID 2>/dev/null
exit 1

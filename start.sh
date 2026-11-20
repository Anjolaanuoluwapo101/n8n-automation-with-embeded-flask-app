#!/bin/sh

# ── 1. nginx (reverse proxy on :7860) ──────────────────────────────────────
# Routes GET /health → Flask:5000, everything else (n8n UI + API + WS) → n8n:5678.
nginx -g 'daemon off;' &
NGINX_PID=$!

# ── 2. n8n (internal :5678) ────────────────────────────────────────────────
n8n start &
N8N_PID=$!

# ── 3. Flask API (internal :5000) ──────────────────────────────────────────
python3 /app/app.py &
FLASK_PID=$!

# ── Process supervisor: monitor all 3 processes ────────────────────────────
# If any process dies, exit so the container platform restarts it.
while kill -0 "$NGINX_PID" 2>/dev/null && kill -0 "$N8N_PID" 2>/dev/null && kill -0 "$FLASK_PID" 2>/dev/null; do
    sleep 2
done

kill $NGINX_PID $N8N_PID $FLASK_PID 2>/dev/null
exit 1

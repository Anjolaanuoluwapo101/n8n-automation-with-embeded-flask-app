FROM alpine:latest AS alpine

FROM n8nio/n8n:latest

USER root

# Bring apk in from Alpine
COPY --from=alpine /sbin/apk /sbin/apk
COPY --from=alpine /usr/lib/libapk.so* /usr/lib/

# n8n dirs
RUN mkdir -p /home/node/.n8n && chown -R node:node /home/node/.n8n

# System packages
RUN apk add --no-cache \
    python3 \
    py3-pip \
    chromium \
    nss \
    freetype \
    harfbuzz \
    ca-certificates \
    ttf-freefont

# Skip Playwright's bundled browser, use system Chromium
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

# HF Spaces
ENV N8N_PORT=7860
ENV N8N_LISTEN_ADDRESS=0.0.0.0

# Python deps (no playwright here)
COPY python-app/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt

# Install playwright bindings separately — pinned to version with musl/Alpine wheels
RUN pip3 install --no-cache-dir --break-system-packages "playwright>=1.40,<1.45"

# Copy scraper scripts + Flask app
COPY python-app/ /app/
COPY start.sh /start.sh
RUN chmod +x /start.sh

USER node

EXPOSE 7860

ENTRYPOINT ["/start.sh"]
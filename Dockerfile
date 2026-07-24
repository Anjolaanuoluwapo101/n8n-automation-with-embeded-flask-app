# Use the official n8n image as the base
FROM n8nio/n8n:latest

USER root

# Create the n8n directory and set permissions for the 'node' user
RUN mkdir -p /home/node/.n8n && chown -R node:node /home/node/.n8n

# --- Python + scraper stack -------------------------------------------------
# n8n's image is Alpine-based, hence apk not apt-get.
RUN apk add --no-cache \
        python3 \
        py3-pip \
        # Playwright/Chromium runtime deps on Alpine
        chromium \
        nss \
        freetype \
        harfbuzz \
        ca-certificates \
        ttf-freefont

COPY python-app /app/python-app
RUN pip3 install --break-system-packages --no-cache-dir -r /app/python-app/requirements.txt

# Point Playwright at the Alpine chromium package instead of downloading its
# own (the bundled Playwright chromium build doesn't run on musl/Alpine).
ENV PLAYWRIGHT_BROWSERS_PATH=0
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium-browser

RUN chown -R node:node /app/python-app

# --- n8n / HF Spaces port config --------------------------------------------
ENV N8N_PORT=7860
ENV N8N_LISTEN_ADDRESS=0.0.0.0

COPY start.sh /start.sh
RUN chmod +x /start.sh

USER node

EXPOSE 7860
ENTRYPOINT ["/start.sh"]

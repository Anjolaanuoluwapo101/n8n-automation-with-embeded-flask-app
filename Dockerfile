FROM alpine:latest AS alpine

FROM n8nio/n8n:latest

USER root

# Bring apk in from Alpine
COPY --from=alpine /sbin/apk /sbin/apk
COPY --from=alpine /usr/lib/libapk.so* /usr/lib/

# n8n dirs
RUN mkdir -p /home/node/.n8n && chown -R node:node /home/node/.n8n

# System packages — chromium-chromedriver replaces playwright entirely
RUN apk add --no-cache \
    python3 \
    py3-pip \
    chromium \
    chromium-chromedriver \
    nss \
    freetype \
    harfbuzz \
    ca-certificates \
    ttf-freefont

# Point scripts at system Chromium (reusing same env var name so no script changes needed)
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver
ENV N8N_PORT=7860
ENV N8N_LISTEN_ADDRESS=0.0.0.0

# Python deps — no playwright in here anymore
COPY python-app/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt

# selenium replaces playwright — no C++ compilation, works on musl/Python 3.14
RUN pip3 install --no-cache-dir --break-system-packages selenium

COPY python-app/ /app/
COPY start.sh /start.sh
RUN chmod +x /start.sh

USER node

EXPOSE 7860

ENTRYPOINT ["/start.sh"]

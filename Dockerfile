FROM alpine:latest AS alpine

FROM n8nio/n8n:latest

USER root

COPY --from=alpine /sbin/apk /sbin/apk
COPY --from=alpine /usr/lib/libapk.so* /usr/lib/

RUN mkdir -p /home/node/.n8n && chown -R node:node /home/node/.n8n

RUN apk add --no-cache \
    python3 \
    py3-pip \
    chromium \
    nss \
    freetype \
    harfbuzz \
    ca-certificates \
    ttf-freefont

ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium
ENV N8N_PORT=7860
ENV N8N_LISTEN_ADDRESS=0.0.0.0

COPY python-app/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt

# Install playwright from source — bypasses PyPI wheel restriction on Python 3.14
RUN apk add --no-cache git && \
    pip3 install --no-cache-dir --break-system-packages \
    "git+https://github.com/microsoft/playwright-python.git@v1.44.0" && \
    apk del git

COPY python-app/ /app/
COPY start.sh /start.sh
RUN chmod +x /start.sh

USER node

EXPOSE 7860

ENTRYPOINT ["/start.sh"]
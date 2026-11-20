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
    chromium-chromedriver \
    nss \
    freetype \
    harfbuzz \
    ca-certificates \
    ttf-freefont \
    nginx \
    libpq

ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

ENV N8N_PORT=5678
ENV N8N_LISTEN_ADDRESS=0.0.0.0

COPY nginx.conf /etc/nginx/nginx.conf

COPY python-app/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages selenium

COPY python-app/ /app/

# Fix: create outputs dir and nginx runtime directories as root before switching user
RUN mkdir -p /app/outputs \
             /var/lib/nginx/tmp \
             /var/log/nginx \
             /run/nginx \
             /tmp/client_body \
             /tmp/proxy \
             /tmp/fastcgi \
             /tmp/uwsgi \
             /tmp/scgi && \
    chown -R node:node /app /var/lib/nginx /var/log/nginx /run/nginx /etc/nginx /tmp

COPY start.sh /start.sh
RUN chmod +x /start.sh

USER node

EXPOSE 7860

ENTRYPOINT ["/start.sh"]
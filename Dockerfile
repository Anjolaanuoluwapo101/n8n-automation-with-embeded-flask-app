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

# Application ports
ENV N8N_PORT=5678
ENV N8N_LISTEN_ADDRESS=0.0.0.0
ENV NODE_OPTIONS="--max-old-space-size=460"

# Default Neon PostgreSQL connection parameters
ENV DB_TYPE=postgresdb
ENV DB_POSTGRESDB_HOST=ep-spring-cell-be7b894g.c-9.us-east-2.aws.neon.tech
ENV DB_POSTGRESDB_PORT=5432
ENV DB_POSTGRESDB_DATABASE=neondb
ENV DB_POSTGRESDB_USER=neondb_owner
ENV DB_POSTGRESDB_PASSWORD=npg_uSRwq5GiC8KQ
ENV DB_POSTGRESDB_SSL_REJECT_UNAUTHORIZED=false

ENV DATABASE_URL="postgresql://neondb_owner:npg_uSRwq5GiC8KQ@ep-spring-cell-be7b894g-pooler.c-9.us-east-2.aws.neon.tech/neondb?channel_binding=require&sslmode=require"
ENV DATABASE_URL_UNPOOLED="postgresql://neondb_owner:npg_uSRwq5GiC8KQ@ep-spring-cell-be7b894g.c-9.us-east-2.aws.neon.tech/neondb?channel_binding=require&sslmode=require"
ENV NEON_BRANCH=production
ENV NEON_BRANCH_ID=br-divine-hall-bezmuv9o
ENV N8N_ENCRYPTION_KEY="z+Hl2X7EYGtuOWjiZiomgEeDKcZCgzd5AvzB+jEeVKM="
ENV EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS=false
ENV EXECUTIONS_DATA_SAVE_ON_ERROR=all
ENV EXECUTIONS_DATA_SAVE_ON_SUCCESS=none

COPY nginx.conf /etc/nginx/nginx.conf

COPY python-app/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages selenium

COPY python-app/ /app/

# Create outputs dir and nginx runtime directories as root before switching user
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

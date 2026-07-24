FROM alpine:3.19

RUN apk add --no-cache \
    nodejs \
    npm \
    python3 \
    py3-pip \
    chromium \
    nss \
    freetype \
    harfbuzz \
    ca-certificates \
    ttf-freefont

RUN npm install -g n8n

ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

COPY python-app/requirements.txt /app/requirements.txt
RUN pip3 install --break-system-packages --no-cache-dir -r /app/requirements.txt

COPY python-app/ /app/
COPY start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 7860 5000

ENTRYPOINT ["/start.sh"]
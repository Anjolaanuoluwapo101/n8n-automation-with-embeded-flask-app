FROM n8nio/n8n:latest

USER root

RUN ["/sbin/apk", "add", "--no-cache", \
    "python3", \
    "py3-pip", \
    "chromium", \
    "nss", \
    "freetype", \
    "harfbuzz", \
    "ca-certificates", \
    "ttf-freefont"]

ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

COPY python-app/requirements.txt /app/requirements.txt
RUN ["/usr/bin/pip3", "install", "--break-system-packages", "--no-cache-dir", "-r", "/app/requirements.txt"]

COPY python-app/ /app/
COPY start.sh /start.sh
RUN ["/bin/chmod", "+x", "/start.sh"]

USER node
ENTRYPOINT ["/start.sh"]
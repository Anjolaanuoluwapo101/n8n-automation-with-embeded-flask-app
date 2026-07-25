FROM alpine:3.19 AS pybuilder

RUN apk add --no-cache \
    python3 \
    py3-pip \
    chromium \
    nss \
    freetype \
    harfbuzz \
    ca-certificates \
    ttf-freefont

COPY python-app/requirements.txt /requirements.txt
RUN pip3 install --break-system-packages --no-cache-dir \
    -r /requirements.txt \
    --target /pypackages

# ── Final image: official n8n (pre-compiled isolated-vm intact) ──
FROM n8nio/n8n:latest

USER root

# Python runtime
COPY --from=pybuilder /usr/bin/python3 /usr/bin/python3
COPY --from=pybuilder /usr/lib/python3.11 /usr/lib/python3.11
COPY --from=pybuilder /usr/lib/python3 /usr/lib/python3
COPY --from=pybuilder /pypackages /pypackages

# Chromium + runtime libs
COPY --from=pybuilder /usr/bin/chromium-browser /usr/bin/chromium
COPY --from=pybuilder /usr/lib/chromium /usr/lib/chromium
COPY --from=pybuilder /usr/lib/libharfbuzz.so.0 /usr/lib/libharfbuzz.so.0
COPY --from=pybuilder /usr/lib/libfreetype.so.6 /usr/lib/libfreetype.so.6
COPY --from=pybuilder /usr/lib/libnss3.so /usr/lib/libnss3.so
COPY --from=pybuilder /usr/lib/libnssutil3.so /usr/lib/libnssutil3.so
COPY --from=pybuilder /usr/lib/libsmime3.so /usr/lib/libsmime3.so
COPY --from=pybuilder /usr/lib/libssl3.so /usr/lib/libssl3.so
COPY --from=pybuilder /usr/share/fonts /usr/share/fonts
COPY --from=pybuilder /etc/ssl/certs /etc/ssl/certs

ENV PYTHONPATH=/pypackages
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

COPY python-app/ /app/
COPY start.sh /start.sh
RUN ["chmod", "+x", "/start.sh"]

USER node
ENTRYPOINT ["/start.sh"]
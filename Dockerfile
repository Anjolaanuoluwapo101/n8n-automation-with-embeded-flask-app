FROM n8nio/n8n:latest

USER root

RUN cat /etc/os-release || cat /etc/alpine-release || echo "unknown"
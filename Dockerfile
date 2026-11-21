FROM n8nio/n8n:2.48.7

USER root
RUN mkdir -p /home/node/.n8n && chown -R node:node /home/node/.n8n

# ---------------------------------------------------------------------------
# Tuned for Render FREE (512 MB RAM / 0.1 CPU).
# Budget: n8n main ~290 MB heap + ~90 MB native, task runner ~100 MB only while
# a Code node is running (it exits when idle).
# ---------------------------------------------------------------------------

ENV N8N_PORT=7860
ENV N8N_LISTEN_ADDRESS=0.0.0.0
ENV NODE_OPTIONS="--max-old-space-size=288"

# Neon Postgres (password + encryption key are set in the Render dashboard)
ENV DB_TYPE=postgresdb
ENV DB_POSTGRESDB_HOST=ep-spring-cell-be7b894g.c-9.us-east-2.aws.neon.tech
ENV DB_POSTGRESDB_PORT=5432
ENV DB_POSTGRESDB_DATABASE=neondb
ENV DB_POSTGRESDB_USER=neondb_owner
ENV DB_POSTGRESDB_POOL_SIZE=2
# Neon free compute sleeps; give the first connection time to wake it
ENV DB_POSTGRESDB_CONNECTION_TIMEOUT=30000

# Execution data: keep as little as possible in memory and in the DB
ENV EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS=false
ENV EXECUTIONS_DATA_SAVE_ON_ERROR=all
ENV EXECUTIONS_DATA_SAVE_ON_SUCCESS=none
ENV EXECUTIONS_DATA_SAVE_ON_PROGRESS=false
ENV EXECUTIONS_DATA_PRUNE=true
ENV EXECUTIONS_DATA_MAX_AGE=168
ENV N8N_DEFAULT_BINARY_DATA_MODE=filesystem
ENV N8N_PAYLOAD_SIZE_MAX=8

# One workflow at a time - parallel runs are what tip 512 MB over
ENV N8N_CONCURRENCY_PRODUCTION_LIMIT=1

# Turn off everything not needed
ENV N8N_DIAGNOSTICS_ENABLED=false
ENV N8N_VERSION_NOTIFICATIONS_ENABLED=false
ENV N8N_TEMPLATES_ENABLED=false
ENV N8N_HIRING_BANNER_ENABLED=false
ENV N8N_PERSONALIZATION_ENABLED=false
ENV N8N_PUBLIC_API_DISABLED=true
ENV N8N_UNVERIFIED_PACKAGES_ENABLED=false

# Task runner (needed for Code nodes): small, one task, shut down when idle
ENV N8N_RUNNERS_MODE=internal
ENV N8N_RUNNERS_MAX_CONCURRENCY=1
ENV N8N_RUNNERS_MAX_OLD_SPACE_SIZE=96
ENV N8N_RUNNERS_AUTO_SHUTDOWN_TIMEOUT=15
ENV N8N_RUNNERS_TASK_TIMEOUT=300
ENV N8N_RUNNERS_TASK_REQUEST_TIMEOUT=300

# (Removed N8N_COMPRESSION_NODE_MAX_DECOMPRESSED_SIZE_BYTES=2GB - letting the
#  Compression node inflate up to 2 GB is a guaranteed OOM on a 512 MB box.)

USER node
EXPOSE 7860
ENTRYPOINT ["n8n", "start"]

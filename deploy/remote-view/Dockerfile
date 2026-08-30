FROM python:3.12-slim
WORKDIR /app
COPY remote.py .
# Host injects $PORT (Render/Railway) — bind it; default 8899 for local `docker run`.
# Store is ephemeral /tmp so no paid persistent disk is needed: the machine
# re-pushes the summary on every heartbeat, so a restart self-heals within a beat.
CMD ["sh", "-c", "python3 remote.py serve --port ${PORT:-8899} --ingest ${INGEST_TOKEN} --view ${VIEW_SECRET} --store /tmp/latest.json"]

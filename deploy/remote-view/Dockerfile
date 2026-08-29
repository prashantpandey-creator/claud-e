FROM python:3.12-slim
WORKDIR /app
COPY remote.py .
CMD ["sh", "-c", "python3 remote.py serve --port ${PORT:-8899} --ingest ${INGEST_TOKEN} --view ${VIEW_SECRET} --store /data/latest.json"]

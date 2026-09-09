FROM python:3.13-slim-bookworm

LABEL org.opencontainers.image.title="Content Bot" \
      org.opencontainers.image.version="0.2.0" \
      org.opencontainers.image.description="Telegram editorial bot: approve/reject drafts and publish to a channel"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    HOME=/tmp \
    TMPDIR=/tmp

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10004 contentbot \
    && useradd --uid 10004 --gid contentbot --home-dir /nonexistent --no-create-home contentbot

WORKDIR /app
COPY content/content_pipeline ./content_pipeline
COPY content-bot/requirements.txt ./content-bot/requirements.txt
RUN pip install --no-cache-dir -r content-bot/requirements.txt
COPY content-bot/content_bot ./content_bot

USER 10004:10004
CMD ["python", "-m", "content_bot"]

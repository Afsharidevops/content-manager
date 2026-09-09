FROM python:3.13-slim-bookworm

LABEL org.opencontainers.image.title="Media Studio" \
      org.opencontainers.image.version="0.2.0" \
      org.opencontainers.image.description="Browser automation worker for Google Flow and Gemini media generation jobs"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    HOME=/tmp \
    TMPDIR=/tmp

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10005 mediastudio \
    && useradd --uid 10005 --gid mediastudio --home-dir /nonexistent --no-create-home mediastudio

WORKDIR /app
COPY media-studio/requirements.txt ./media-studio/requirements.txt
RUN pip install --no-cache-dir -r media-studio/requirements.txt \
    && playwright install --with-deps chromium \
    && rm -rf /root/.cache/pip
COPY media-studio/media_studio ./media_studio
COPY extensions ./extensions

USER 10005:10005
CMD ["python", "-m", "media_studio"]

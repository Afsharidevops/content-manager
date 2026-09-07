FROM python:3.13.5-slim-bookworm@sha256:4c2cf9917bd1cbacc5e9b07320025bdb7cdf2df7b0ceaccb55e9dd7e30987419

LABEL org.opencontainers.image.title="Hermes Execution Broker" \
      org.opencontainers.image.version="0.1.3" \
      org.opencontainers.image.description="Hermes execution brokers, independent Telegram approver, and separate execution-admin boundary"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    HOME=/tmp \
    TMPDIR=/tmp

# openssh-client is needed only in ssh mode; the docker-mode container never
# receives a profile mount, so it has no key to use it with.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends openssh-client openssl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10003 broker \
    && useradd --uid 10003 --gid broker --home-dir /nonexistent --no-create-home broker

WORKDIR /app
COPY src ./src
COPY --chmod=0555 ssh-askpass.py /usr/local/libexec/hermes-ssh-askpass

USER 10003:10003
EXPOSE 8750 8751

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=5 \
  CMD ["python", "-c", "import os, urllib.request; port=os.environ.get('BROKER_PORT', '8750'); urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=3)"]

CMD ["sh", "-c", "umask 077 && exec python -m broker.server"]

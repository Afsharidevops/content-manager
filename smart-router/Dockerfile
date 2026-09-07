FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --gid 10001 smart-router \
 && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/smart-router smart-router \
 && mkdir -p /app /data /policy \
 && chown -R 10001:10001 /app /data /policy

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

USER 10001:10001
VOLUME ["/data"]
EXPOSE 8080
CMD ["python", "-m", "smart_router.main"]

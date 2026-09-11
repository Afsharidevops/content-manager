# Operator panel for the content stack.
#
# Runs inside the compose project it manages: the Docker CLI talks to the
# mounted Docker socket, and the repository is mounted at the identical host
# path so `docker compose` resolves bind mounts exactly like a host invocation.
FROM python:3.13-alpine

# docker-cli/docker-cli-compose let the panel drive the mounted Docker socket.
# bash runs manage.sh, curl/openssl are used by its status and secret helpers,
# and GNU tar (the BusyBox tar cannot mix two -C options) lets the Backups
# action archive the stack exactly like the host command does. PyYAML is
# installed with pip so the wheel always matches the image's Python version
# (the Alpine py3-yaml package can target another).
RUN apk add --no-cache docker-cli docker-cli-compose bash curl openssl tar \
 && pip install --no-cache-dir pyyaml

WORKDIR /app
COPY panel/ /app/panel/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp

EXPOSE 8899

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s \
    CMD wget -q -O /dev/null http://127.0.0.1:8899/healthz || exit 1

ENTRYPOINT ["python3", "-m", "panel.server"]
CMD ["--bind", "0.0.0.0", "--port", "8899"]

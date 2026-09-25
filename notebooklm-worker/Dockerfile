ARG NOTEBOOKLM_WORKER_BASE_IMAGE=afsharidevops/notebooklm-worker-base:0.1.0
FROM ${NOTEBOOKLM_WORKER_BASE_IMAGE}

LABEL org.opencontainers.image.title="NotebookLM Worker" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.description="Browser automation worker that produces videos with Google NotebookLM Video Overviews"

WORKDIR /app
COPY notebooklm-worker/app ./app
COPY notebooklm-worker/scripts ./scripts

USER 10006:10006
CMD ["python", "-m", "app"]

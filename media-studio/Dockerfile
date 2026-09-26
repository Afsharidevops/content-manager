ARG MEDIA_STUDIO_BASE_IMAGE=afsharidevops/media-studio-base:0.5.1
FROM ${MEDIA_STUDIO_BASE_IMAGE}

LABEL org.opencontainers.image.title="Media Studio" \
      org.opencontainers.image.version="0.5.1" \
      org.opencontainers.image.description="Browser automation worker for Google Flow and Gemini media generation jobs"

WORKDIR /app
COPY media-studio/media_studio ./media_studio
COPY extensions ./extensions

USER 10005:10005
CMD ["xvfb-run", "-a", "--server-args=-screen 0 1920x1080x24", "python", "-m", "media_studio"]

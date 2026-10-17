# syntax=docker/dockerfile:1
# The VTT stack's web image (M6, handoff 053; VTT-ADR-0003).
#
# Stage 1 builds the client with Node and checks the bundle; stage 2 is the
# Python app with exactly that output. The bundle is never committed: this
# build is the only place it is produced for an image.
#
# Production (main) is unchanged and still builds Python-only with nixpacks;
# this image serves the local stack and, once approved, staging.

FROM node:24.12.0-bookworm-slim AS client
WORKDIR /src/frontend/vtt
COPY frontend/vtt/package.json frontend/vtt/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/vtt/ ./
COPY contracts/ /src/contracts/
RUN npm run build && npm run check:bundle

FROM python:3.11.9-slim-bookworm AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.txt
COPY . .
COPY --from=client /src/frontend/vtt/dist /app/frontend/vtt/dist
# The entrypoint's line endings are normalized here as well as by
# .gitattributes: a Windows working tree can still hold CRLF, and a CRLF
# shebang makes the container fail with "no such file or directory".
RUN useradd --system --uid 10001 --home-dir /app app \
    && mkdir -p /data \
    && chown -R app:app /data \
    && sed -i 's/\r$//' /app/docker/entrypoint.sh \
    && chmod 0755 /app/docker/entrypoint.sh
USER app
ENV DATA_DIR=/data \
    VTT_ENABLED=1 \
    VTT_DIST_DIR=/app/frontend/vtt/dist \
    PORT=8000
EXPOSE 8000
VOLUME ["/data"]
ENTRYPOINT ["/app/docker/entrypoint.sh"]

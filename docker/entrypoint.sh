#!/bin/sh
# Web container start (M6): apply migrations, then run the app exactly as
# production does (Procfile): ONE gevent worker, which the SSE hub and the
# in-process VTT fan-out both require. Never raise --workers.
set -eu

if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL is required for the VTT stack" >&2
  exit 1
fi
if [ -z "${SECRET_KEY:-}" ]; then
  echo "SECRET_KEY is required (sessions, table grants and version tokens derive from it)" >&2
  exit 1
fi

python -m alembic upgrade head

# --no-control-socket: gunicorn's control socket would need a writable /app.
exec gunicorn app:app \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers 1 \
  --worker-class gevent \
  --worker-connections 1000 \
  --timeout 120 \
  --no-control-socket

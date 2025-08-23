#!/usr/bin/env bash
set -euo pipefail

# Entrypoint for the scalable mock OCR container
# Build the gunicorn command using environment variables so values like
# UVICORN_WORKERS are expanded at runtime on container start.

APP_MODULE=${APP_MODULE:-tools.mock_remote_ocr.mock_remote_ocr:app}
HOST=${HOST:-0.0.0.0}
PORT=${PORT:-8090}
UVICORN_WORKERS=${UVICORN_WORKERS:-2}
GUNICORN_TIMEOUT=${GUNICORN_TIMEOUT:-120}

# If running in GPU-enabled image, user can set MOCK_OCR_FORCE_GPU=1
if [ -n "${MOCK_OCR_FORCE_GPU:-}" ]; then
  echo "MOCK_OCR_FORCE_GPU=${MOCK_OCR_FORCE_GPU}"
fi

if [ "$#" -gt 0 ] && [ "$1" = "gunicorn" ]; then
  # Construct the final command with expanded environment variables
  set -- gunicorn -k uvicorn.workers.UvicornWorker -w "${UVICORN_WORKERS}" \
    --timeout "${GUNICORN_TIMEOUT}" --bind "0.0.0.0:${PORT}" "${APP_MODULE}"
  echo "Starting: $*"
fi

# Exec whatever command was provided or the constructed gunicorn command
exec "$@"

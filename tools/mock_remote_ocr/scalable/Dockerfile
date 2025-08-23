FROM python:3.12-slim AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install build deps
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
     build-essential \
     git \
     libgl1-mesa-dri \
     libglib2.0-0 \
     libsm6 \
     libxext6 \
     libxrender1 \
 && rm -rf /var/lib/apt/lists/*

# Copy poetry/pyproject if present to cache deps
COPY pyproject.toml poetry.lock* /app/

# Install pip and build dependencies
RUN pip install --upgrade pip setuptools wheel

# Install production dependencies into a wheelhouse
RUN pip wheel --wheel-dir=/wheels . || true

# ---- Final stage ----
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UVICORN_WORKERS=2 \
    GUNICORN_WORKER_CLASS=uvicorn.workers.UvicornWorker \
    GUNICORN_TIMEOUT=120

WORKDIR /app

# System deps for image decoding and optional GPU libs
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
     ffmpeg \
     libgl1-mesa-dri \
     libglib2.0-0 \
     libsm6 \
     libxext6 \
     libxrender1 \
 && rm -rf /var/lib/apt/lists/*

# Copy application code
COPY . /app

# Install runtime deps from wheelhouse if present, otherwise fallback to pip install
COPY --from=build /wheels /wheels
RUN if [ -d /wheels ] && [ "$(ls -A /wheels)" ]; then \
            pip install --no-cache-dir /wheels/*; \
        else \
            pip install --no-cache-dir .; \
        fi

# Ensure runtime server packages are installed in the final image
RUN pip install --no-cache-dir "easyocr" "gunicorn>=20.0" "uvicorn[standard]"

# Pre-download EasyOCR models at build time into the optics home so the
# runtime doesn't need to fetch models (avoids runtime downloads and OOMs).
RUN mkdir -p /home/optics && \
    python - <<'PY'
import os
os.environ['HOME'] = '/home/optics'
try:
    from easyocr import Reader
    # instantiate reader to force model download for English
    Reader(['en'], gpu=False)
    print('EasyOCR models downloaded')
except Exception as e:
    print('EasyOCR model bootstrap failed:', e)
    raise
PY

# Expose port
EXPOSE 8090

# Entrypoint and default command use env vars for tuning
COPY tools/mock_remote_ocr/scalable/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Add a non-root user for security, create a home dir and ensure ownership
RUN groupadd -r optics \
 && useradd -r -g optics -m -d /home/optics optics \
 && mkdir -p /home/optics/.EasyOCR/model \
 && chown -R optics:optics /app /entrypoint.sh /home/optics
USER optics

ENV APP_MODULE=tools.mock_remote_ocr.mock_remote_ocr:app \
    HOST=0.0.0.0 \
    PORT=8090

ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "-w", "${UVICORN_WORKERS}", "--timeout", "${GUNICORN_TIMEOUT}", "--bind", "0.0.0.0:${PORT}", "${APP_MODULE}"]

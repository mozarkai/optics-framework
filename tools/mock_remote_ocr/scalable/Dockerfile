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

# Copy the full source: pip needs it to build a real optics-framework wheel
# below, not just resolve metadata from pyproject.toml.
COPY optics_framework /app/optics_framework
COPY pyproject.toml README.md /app/

# Install pip and build dependencies
RUN pip install --upgrade --only-binary=:all: pip==26.1.2 setuptools==83.0.0 wheel==0.47.0

# Build this local, unpublished project into a wheel, plus a wheelhouse of
# every third-party dependency it needs. --only-binary=:all: still allows
# building the local project itself (pip always builds local directories
# you point it at directly) while forbidding any of its third-party
# dependencies from building from source.
RUN pip wheel --only-binary=:all: --wheel-dir=/wheels .

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
COPY optics_framework /app/optics_framework
COPY tools/mock_remote_ocr /app/tools/mock_remote_ocr
COPY pyproject.toml /app/

# Install optics-framework from the wheel just built, pinned to the exact
# version that wheel actually is, resolving its dependencies only from the
# wheelhouse built alongside it (--no-index) - never from PyPI.
COPY --from=build /wheels /wheels
RUN WHEEL_VERSION="$(basename "$(ls /wheels/optics_framework-*.whl)" .whl | cut -d- -f2)" && \
    pip install --no-cache-dir --only-binary=:all: --no-index --find-links=/wheels \
        "optics-framework==${WHEEL_VERSION}" && \
    pip install --no-cache-dir --only-binary=:all: \
        easyocr==1.7.2 gunicorn==26.0.0 "uvicorn[standard]==0.37.0" && \
    mkdir -p /home/optics && \
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

# Add a non-root user for security, create a home dir, ensure ownership, and set entrypoint permissions
RUN chmod +x /entrypoint.sh \
 && groupadd -r optics \
 && useradd -r -g optics -m -d /home/optics optics \
 && mkdir -p /home/optics/.EasyOCR/model \
 && chown -R optics:optics /app /entrypoint.sh /home/optics
USER optics

ENV APP_MODULE=tools.mock_remote_ocr.mock_remote_ocr:app \
    HOST=0.0.0.0 \
    PORT=8090

ENTRYPOINT ["/entrypoint.sh"]

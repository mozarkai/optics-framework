# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Create a non-root user
RUN useradd --create-home --shell /bin/bash appuser

# Set working directory
WORKDIR /app

# Build arguments for vision backend
ARG VISION_BACKEND=easyocr
ENV VISION_BACKEND=${VISION_BACKEND}


# Install system dependencies (first layer for cache)
RUN apt-get update && apt-get install --no-install-recommends -y \
    build-essential \
    curl \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install common Python packages (second layer for cache)
RUN pip install --no-cache-dir \
    appium-python-client

# Install optics-framework and vision backend (late layer for cache efficiency)
RUN pip install --no-cache-dir optics-framework \
    && if [ "$VISION_BACKEND" = "easyocr" ]; then \
        pip install --no-cache-dir easyocr && \
        python3 -c "import easyocr; easyocr.Reader(['en'], download_enabled=True)"; \
    elif [ "$VISION_BACKEND" = "google-vision" ]; then \
        pip install --no-cache-dir google-cloud-vision; \
    elif [ "$VISION_BACKEND" = "pytesseract" ]; then \
        pip install --no-cache-dir pytesseract; \
    fi

# If using google-vision, user must mount service account json and set GOOGLE_APPLICATION_CREDENTIALS
# Example: docker run -e GOOGLE_APPLICATION_CREDENTIALS=/app/service-account.json -v /path/to/service-account.json:/app/service-account.json ...

# Set permissions
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

EXPOSE 8000

# Allow dynamic worker count via UVICORN_WORKERS env variable (default 1)
ENV UVICORN_WORKERS=1

# Use exec form for CMD with shell for env var substitution
CMD ["/bin/sh", "-c", "exec optics serve --host 0.0.0.0 --port 8000 --workers \"${UVICORN_WORKERS:-1}\""]

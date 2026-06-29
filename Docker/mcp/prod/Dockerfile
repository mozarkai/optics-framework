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
    xvfb \
    dbus-x11 \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv \
    && uv pip install --system appium-python-client playwright \
    && playwright install-deps chromium firefox

# Install optics-framework with MCP extra and vision backend (late layer for cache efficiency)
RUN uv pip install --system "optics-framework[mcp]" \
    && if [ "$VISION_BACKEND" = "easyocr" ]; then \
        uv pip install --system easyocr; \
    elif [ "$VISION_BACKEND" = "google-vision" ]; then \
        uv pip install --system google-cloud-vision; \
    elif [ "$VISION_BACKEND" = "pytesseract" ]; then \
        uv pip install --system pytesseract; \
    fi

# If using google-vision, user must mount service account json and set GOOGLE_APPLICATION_CREDENTIALS
# Example: docker run -e GOOGLE_APPLICATION_CREDENTIALS=/app/service-account.json -v /path/to/service-account.json:/app/service-account.json ...

# Set permissions
RUN chown -R appuser:appuser /app

# Switch to non-root user to install Playwright browsers in user's cache
USER appuser

# Pre-download EasyOCR models as appuser (must match runtime user's ~/.EasyOCR/)
RUN if [ "$VISION_BACKEND" = "easyocr" ]; then \
    python3 -c "import easyocr; easyocr.Reader(['en'], download_enabled=True)"; \
    fi

# Install Playwright browsers (Chromium and Firefox) as appuser (must be done as the user who will run the app)
RUN playwright install chromium firefox

# Note: We stay as appuser for the rest of the Dockerfile

EXPOSE 8090

# Allow dynamic port via MCP_PORT env variable (default 8090)
ENV MCP_PORT=8090

# Set DISPLAY for X11 (xvfb will provide virtual display)
ENV DISPLAY=:99

# Use exec form for CMD with shell for env var substitution
# Start xvfb in the background, then run optics mcp over HTTP
CMD ["/bin/sh", "-c", "Xvfb :99 -screen 0 1280x1024x24 > /dev/null 2>&1 & exec optics mcp --transport http --host 0.0.0.0 --port \"${MCP_PORT:-8090}\""]

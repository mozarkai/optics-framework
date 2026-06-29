# syntax=docker/dockerfile:1

FROM python:3.12-slim

# Create a non-root user
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Accept the .whl filename as a build argument
ARG WHL_FILE
ARG VISION_BACKEND=easyocr

ENV VISION_BACKEND=${VISION_BACKEND}
ENV WHL_FILE=${WHL_FILE}

# Copy wheel(s) from dist/ (empty WHL_FILE copies all of dist/, matching Docker/dev/Dockerfile)
COPY dist/${WHL_FILE} /app/


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

# Install common Python packages and optics-framework wheel with MCP extra and vision backend
RUN pip install --no-cache-dir \
   appium-python-client playwright \
   && playwright install-deps chromium firefox \
   && WHEEL=$(if [ -n "$WHL_FILE" ]; then echo "/app/${WHL_FILE}"; else ls /app/*.whl 2>/dev/null | head -1; fi) \
   && if [ -z "$WHEEL" ] || [ ! -f "$WHEEL" ]; then \
        echo "ERROR: No wheel in dist/. Run 'poetry build' first or set WHL_FILE to the .whl filename."; \
        exit 1; \
      fi \
   && pip install --no-cache-dir "${WHEEL}[mcp]" \
   && if [ "$VISION_BACKEND" = "easyocr" ]; then \
      pip install --no-cache-dir easyocr; \
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

# Pre-download EasyOCR models as appuser (must match runtime user's ~/.EasyOCR/)
RUN if [ "$VISION_BACKEND" = "easyocr" ]; then \
    python3 -c "import easyocr; easyocr.Reader(['en'], download_enabled=True)"; \
    fi

# Install Playwright browsers (Chromium and Firefox) as appuser (must be done as the user who will run the app)
RUN playwright install chromium firefox

EXPOSE 8090

# Allow dynamic port via MCP_PORT env variable (default 8090)
ENV MCP_PORT=8090

# Set DISPLAY for X11 (xvfb will provide virtual display)
ENV DISPLAY=:99

# Use exec form for CMD with shell for env var substitution
# Start xvfb in the background, then run optics mcp over HTTP
CMD ["/bin/sh", "-c", "Xvfb :99 -screen 0 1280x1024x24 > /dev/null 2>&1 & exec optics mcp --transport http --host 0.0.0.0 --port \"${MCP_PORT:-8090}\""]

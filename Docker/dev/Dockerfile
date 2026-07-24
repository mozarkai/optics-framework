# syntax=docker/dockerfile:1

FROM python:3.12-slim

# Create a non-root user
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Accept the .whl filename as a build argument
ARG WHL_FILE
ENV WHL_FILE=${WHL_FILE}

# Build arguments for vision backend
ARG VISION_BACKEND=easyocr
ENV VISION_BACKEND=${VISION_BACKEND}

# Copy the wheel into the container
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

# Install common Python packages and optics-framework wheel and vision backend.
# All pip installs are pinned to exact versions and restricted to prebuilt
# wheels (--only-binary=:all:) so no dependency's setup.py/build backend runs
# at image build time.
RUN pip install --no-cache-dir --only-binary=:all: \
   appium-python-client==5.3.1 playwright==1.61.0 \
   && playwright install-deps chromium firefox \
   && WHEEL=$(if [ -n "$WHL_FILE" ]; then echo "/app/${WHL_FILE}"; else ls /app/*.whl 2>/dev/null | head -1; fi) \
   && if [ -z "$WHEEL" ] || [ ! -f "$WHEEL" ]; then \
        echo "ERROR: No wheel in dist/. Run 'poetry build' first or set WHL_FILE to the .whl filename."; \
        exit 1; \
      fi \
   && pip install --no-cache-dir --only-binary=:all: "$WHEEL" \
   && if [ "$VISION_BACKEND" = "easyocr" ]; then \
      pip install --no-cache-dir --only-binary=:all: easyocr==1.7.2; \
   elif [ "$VISION_BACKEND" = "google-vision" ]; then \
      pip install --no-cache-dir --only-binary=:all: google-cloud-vision==3.15.0; \
   elif [ "$VISION_BACKEND" = "pytesseract" ]; then \
      pip install --no-cache-dir --only-binary=:all: pytesseract==0.3.13; \
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

EXPOSE 8000

# Allow dynamic worker count via UVICORN_WORKERS env variable (default 1)
ENV UVICORN_WORKERS=1

# Set DISPLAY for X11 (xvfb will provide virtual display)
ENV DISPLAY=:99

# Use exec form for CMD with shell for env var substitution
# Start xvfb in the background, then run optics serve
CMD ["/bin/sh", "-c", "Xvfb :99 -screen 0 1280x1024x24 > /dev/null 2>&1 & exec optics serve --host 0.0.0.0 --port 8000 --workers \"${UVICORN_WORKERS:-1}\""]

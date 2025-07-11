# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Create a non-root user
RUN useradd --create-home --shell /bin/bash appuser

# Set working directory
WORKDIR /app

# Install system dependencies and Python packages, then clean up
RUN apt-get update && apt-get install --no-install-recommends -y \
    build-essential \
    curl \
    libgl1 \
    libglib2.0-0 \
 && pip install --no-cache-dir \
    appium-python-client \
    easyocr \
    fastapi \
    optics-framework \
    uvicorn \
 && rm -rf /var/lib/apt/lists/* \
 && chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

EXPOSE 8000

CMD ["optics", "serve", "--host", "0.0.0.0", "--port", "8000"]

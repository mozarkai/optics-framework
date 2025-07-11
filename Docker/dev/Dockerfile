# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for cv2, etc.
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Accept the .whl filename as a build argument
ARG WHL_FILE
ENV WHL_FILE=${WHL_FILE}

# Copy the specified wheel into the container
COPY dist/${WHL_FILE} /app/

# Install all at once
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    libgl1 \
    libglib2.0-0 \
 && pip install --no-cache-dir "/app/${WHL_FILE}" \
    appium-python-client \
    easyocr \
    fastapi \
    uvicorn \
 && rm -rf /var/lib/apt/lists/*

EXPOSE 8000

CMD ["optics", "serve", "--host", "0.0.0.0", "--port", "8000"]

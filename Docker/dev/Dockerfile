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

# Install the wheel
RUN pip install "/app/${WHL_FILE}"

# Optional: Install extra dependencies if needed
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt || true

EXPOSE 8000

CMD ["optics", "serve", "--host", "0.0.0.0", "--port", "8000"]

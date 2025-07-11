# Base lightweight Python image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies (if needed)
RUN apt-get update && apt-get install -y \
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
 && rm -rf /var/lib/apt/lists/*

# Expose the default port used by optics serve
EXPOSE 8000

# Run the Optics API using the built-in CLI
CMD ["optics", "serve", "--host", "0.0.0.0", "--port", "8000"]

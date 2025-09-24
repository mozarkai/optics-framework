# Dockerfile for Optics Supervisor Tool
# This Dockerfile builds an image that runs the Optics Supervisor API and manages worker processes

FROM python:3.13-slim

# Install system dependencies (if needed)
RUN apt-get update && apt-get install --no-install-recommends -y \
    build-essential \
    curl \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
# Install optics-framework from PyPI (or your package index)
RUN pip install appium-python-client httpx optics-framework trio-websocket websocket-client google-cloud-vision
COPY Docker/mozark-icici.json /app/service-account.json


# Copy supervisor tool only
WORKDIR /app
COPY tools/supervisor_app/supervisor_tool.py /app/

# Expose default supervisor port
EXPOSE 8000

# Entrypoint for supervisor tool
ENTRYPOINT ["python", "supervisor_tool.py"]

# You can override arguments at runtime, e.g.:
# docker run -p 8000:8000 optics-supervisor --workers 4 --base-port 9000 --host 0.0.0.0 --port 8000

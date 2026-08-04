#  Optics Framework API Deployment Guide


This guide outlines how to deploy the Optics API and MCP server using Docker:

1. **Production (REST API)**: Uses the published `optics-framework` from PyPI (see `prod/Dockerfile`)
2. **Development (REST API)**: Uses a locally built `.whl` package from the Poetry-managed `optics-framework` source (see `dev/Dockerfile`)
3. **MCP (HTTP transport)**: Uses separate images under `mcp/` that install `optics-framework[mcp]` and run `optics mcp --transport http` on port **8090**

---

## 📦 Prerequisites

- Docker Desktop installed and running
- Python 3.12+ and [Poetry](https://python-poetry.org/docs/)
- A working internet connection for production mode (to pull from PyPI)
- Access to the `optics-framework` source repo for development mode

---

## ✅ Deployment Mode 1: Production (PyPI Version)

### 📁 Folder Structure
```
prod/
└── Dockerfile
```

### Build
```sh
cd prod/
docker build -t optics-api-prod:1.9.1-easyocr .
```

### Run
```sh
docker run -d -p 8000:8000 --name optics-api-prod optics-api-prod:1.9.1-easyocr
```

#### Vision Backend Selection
The production Dockerfile supports multiple vision backends via the `VISION_BACKEND` build argument (default: `easyocr`). Supported values:

- `easyocr` (default)
- `google-vision`
- `pytesseract`

#### Tagging convention
The Dockerfile always installs the latest `optics-framework` release from PyPI (no version pin), so **tag the image `<optics_version>-<vision_backend>`**, e.g. `1.9.1-easyocr`, using whatever version was actually latest at build time — check `pip index versions optics-framework` (or the [PyPI release history](https://pypi.org/project/optics-framework/#history)) right before building. That way the tag tells you both what OCR backend is baked in and which release it shipped with, instead of leaving both to guesswork later.

Example (Google Vision):
```sh
docker build \
  --build-arg VISION_BACKEND=google-vision \
  -t optics-api-prod:1.9.1-google-vision .
```

If using Google Vision, mount your service account JSON and set the env variable:
```sh
docker run -d -p 8000:8000 \
  -e GOOGLE_APPLICATION_CREDENTIALS=/app/service-account.json \
  -v /path/to/service-account.json:/app/service-account.json \
  --name optics-api-prod optics-api-prod:1.9.1-google-vision
```


## ✅ Deployment Mode 2: Development (Local .whl)

### 📁 Folder Structure
```
dev/
├── Dockerfile
├── dist/
│   └── optics_framework-0.x.x-py3-none-any.whl
```

### Build the .whl package
```sh
cd /path/to/optics-framework
poetry build
```

### Copy the built .whl package into `dev/dist/`:
```sh
cp dist/*.whl /path/to/optics-framework/Docker/dev/dist/
```

### Build (specify the .whl filename)
The wheel filename already pins the exact `optics-framework` version being installed — carry that version, plus the vision backend, into the image tag (same `<optics_version>-<vision_backend>` convention as production):
```sh
cd dev/
docker build \
  --build-arg WHL_FILE=optics_framework-1.9.1-py3-none-any.whl \
  --build-arg VISION_BACKEND=google-vision \
  -t optics-api-dev:1.9.1-google-vision .
```

### Run
```sh
docker run -d -p 8000:8000 --name optics-api-dev optics-api-dev:1.9.1-google-vision
```

#### Vision Backend Selection
Same as production: use `--build-arg VISION_BACKEND=...` to select the backend.

#### Appium Localhost Note
If running Appium on your host machine, use this URL in your config:

```
appium_url: "http://host.docker.internal:4723"
```

---

## ✅ Deployment Mode 3: MCP Server (HTTP transport)

The MCP images expose the optics keyword engine over the [Model Context Protocol](https://modelcontextprotocol.io) at `http://<host>:8090/mcp`. Containerized MCP always uses **HTTP transport** (not stdio). Sessions are **not shared** with `optics serve` even if both containers run.

### 📁 Folder Structure
```
mcp/
├── prod/
│   └── Dockerfile
└── dev/
    └── Dockerfile
```

### Production MCP (PyPI)

#### Build
```sh
cd /path/to/optics-framework
docker build -f Docker/mcp/prod/Dockerfile \
  -t optics-mcp-prod:1.9.1-easyocr .
```

#### Run
```sh
docker run -d -p 8090:8090 --name optics-mcp-prod optics-mcp-prod:1.9.1-easyocr
```

#### Vision Backend Selection
Same as the REST API images: use `--build-arg VISION_BACKEND=...` (`easyocr`, `google-vision`, or `pytesseract`).

Same tagging convention as production: tag as `<optics_version>-<vision_backend>`, using whatever `optics-framework` release was latest on PyPI at build time.

Example (Google Vision):
```sh
docker build -f Docker/mcp/prod/Dockerfile \
  --build-arg VISION_BACKEND=google-vision \
  -t optics-mcp-prod:1.9.1-google-vision .
```

If using Google Vision, mount your service account JSON and set the env variable:
```sh
docker run -d -p 8090:8090 \
  -e GOOGLE_APPLICATION_CREDENTIALS=/app/service-account.json \
  -v /path/to/service-account.json:/app/service-account.json \
  --name optics-mcp-prod optics-mcp-prod:1.9.1-google-vision
```

### Development MCP (Local .whl)

Build the wheel first (`poetry build` from the repo root), then — same as dev REST API, carry the wheel's version and the vision backend into the tag:

```sh
docker build -f Docker/mcp/dev/Dockerfile \
  --build-arg WHL_FILE=optics_framework-1.9.1-py3-none-any.whl \
  --build-arg VISION_BACKEND=google-vision \
  -t optics-mcp-dev:1.9.1-google-vision .
```

```sh
docker run -d -p 8091:8090 --name optics-mcp-dev optics-mcp-dev:1.9.1-google-vision
```

### Docker Compose

From the repo root:

```sh
# Production MCP on host port 8090
docker compose -f Docker/docker-compose.yml up --build mcp

# Development MCP on host port 8091
docker compose -f Docker/docker-compose.yml up --build mcp-dev
```

### Connect an MCP client

Point your MCP client at the container's HTTP endpoint:

```json
{
  "mcpServers": {
    "optics": { "url": "http://127.0.0.1:8090/mcp" }
  }
}
```

Use port **8091** when running the `mcp-dev` compose service.

#### Appium from inside the container

When `start_session` targets Appium on the host machine:

```json
{
  "driver": "appium",
  "url": "http://host.docker.internal:4723",
  "capabilities": { "...": "..." }
}
```

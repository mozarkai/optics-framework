# Docker Deployment

Optics ships container images for its two long-running servers — the REST API
(`optics serve`) and the MCP server (`optics mcp`). The Dockerfiles and the
Compose file live in the repository under
[`Docker/`](https://github.com/mozarkai/optics-framework/tree/main/Docker); this
page is the guide to building and running them.

## The four images

Each server has a **production** image that installs a released
`optics-framework` from PyPI and a **development** image that installs a wheel
you built locally with `poetry build`.

| Dockerfile | Serves | Runs | Container port |
|---|---|---|---|
| [`Docker/prod/Dockerfile`](https://github.com/mozarkai/optics-framework/blob/main/Docker/prod/Dockerfile) | REST API, from PyPI | `optics serve` | 8000 |
| [`Docker/dev/Dockerfile`](https://github.com/mozarkai/optics-framework/blob/main/Docker/dev/Dockerfile) | REST API, from a local wheel | `optics serve` | 8000 |
| [`Docker/mcp/prod/Dockerfile`](https://github.com/mozarkai/optics-framework/blob/main/Docker/mcp/prod/Dockerfile) | MCP, from PyPI | `optics mcp --transport http` | 8090 |
| [`Docker/mcp/dev/Dockerfile`](https://github.com/mozarkai/optics-framework/blob/main/Docker/mcp/dev/Dockerfile) | MCP, from a local wheel | `optics mcp --transport http` | 8090 |

All four are `python:3.12-slim`, run as the non-root `appuser`, and pin
`appium-python-client`, `playwright` and the vision backend to the versions in
`poetry.lock` (read at build time by `scripts/lock_pins.py`). Playwright's
Chromium and Firefox are installed into the image, and an `Xvfb` display is
started on `:99` before the server, so headed browsers work inside the
container.

!!! warning "Build from the repository root"
    Every image copies `poetry.lock` and `scripts/lock_pins.py`, so the build
    context must be the repository root. Always build with `-f Docker/...` from
    the root — `cd Docker/prod && docker build .` cannot work.

## Prerequisites

- Docker (Desktop, or Engine with the Compose plugin)
- A clone of the repository, for the build context
- Python 3.12+ and [Poetry](https://python-poetry.org/docs/), for the
  development images only (to build the wheel)

## Docker Compose

[`Docker/docker-compose.yml`](https://github.com/mozarkai/optics-framework/blob/main/Docker/docker-compose.yml)
defines one service per image, on distinct host ports so several can run at
once. Run it from the repository root:

| Service | Image | Host port | Build args needed |
|---|---|---|---|
| `app` | REST API, PyPI | 8000 | — |
| `dev` | REST API, local wheel | 8001 | a wheel in `dist/` |
| `mcp` | MCP, PyPI | 8090 | — |
| `mcp-dev` | MCP, local wheel | 8091 | a wheel in `dist/` |

```bash
docker compose -f Docker/docker-compose.yml up --build app       # REST API on :8000
docker compose -f Docker/docker-compose.yml up --build mcp       # MCP on :8090
docker compose -f Docker/docker-compose.yml up --build mcp-dev   # MCP on :8091
```

The `dev` and `mcp-dev` services bind-mount the repository at `/app`, so an
edit to a test project on the host is visible inside the container. The `dev`
service also overrides the image's start command, which means it starts
`optics serve` without the `Xvfb` display — run the `app` service instead if
your suite drives a headed browser.

## Building by hand

### REST API, production

```bash
docker build -f Docker/prod/Dockerfile -t optics-api-prod .
docker run -d -p 8000:8000 --name optics-api-prod optics-api-prod
```

Which release it installs is the `OPTICS_FRAMEWORK_VERSION` build argument.
Its default is a fixed version in the Dockerfile, not "the latest", so pass
the version you actually want to deploy:

```bash
docker build -f Docker/prod/Dockerfile \
  --build-arg OPTICS_FRAMEWORK_VERSION=1.10.4 \
  -t optics-api-prod .
```

`optics serve` runs with a single worker by default. Raise it with the
`UVICORN_WORKERS` environment variable at run time:

```bash
docker run -d -p 8000:8000 -e UVICORN_WORKERS=4 --name optics-api-prod optics-api-prod
```

### REST API, development wheel

Build the wheel first — it lands in `dist/`, which is where the image expects
it:

```bash
poetry build
docker build -f Docker/dev/Dockerfile -t optics-api-dev .
docker run -d -p 8000:8000 --name optics-api-dev optics-api-dev
```

With `dist/` holding more than one wheel, name the one you want with the
`WHL_FILE` build argument; otherwise the build picks the first it finds:

```bash
docker build -f Docker/dev/Dockerfile \
  --build-arg WHL_FILE=optics_framework-1.10.4-py3-none-any.whl \
  -t optics-api-dev .
```

### MCP, production

```bash
docker build -f Docker/mcp/prod/Dockerfile -t optics-mcp-prod .
docker run -d -p 8090:8090 --name optics-mcp-prod optics-mcp-prod
```

### MCP, development wheel

```bash
poetry build
docker build -f Docker/mcp/dev/Dockerfile -t optics-mcp-dev .
docker run -d -p 8091:8090 --name optics-mcp-dev optics-mcp-dev
```

Both MCP images install the `[mcp]` extra and bind to `0.0.0.0` inside the
container. `MCP_PORT` changes the port they listen on (default 8090).

## Choosing a vision backend

All four images take a `VISION_BACKEND` build argument. The default is
`easyocr`, whose models are pre-downloaded into the image at build time.

| Value | Installs |
|---|---|
| `easyocr` (default) | `easyocr` |
| `google-vision` | `google-cloud-vision` |
| `pytesseract` | `pytesseract` (the Tesseract binary is already in the image) |

```bash
docker build -f Docker/mcp/prod/Dockerfile \
  --build-arg VISION_BACKEND=google-vision \
  -t optics-mcp-prod .
```

Google Vision reads its credentials from the environment, so mount the service
account JSON and point `GOOGLE_APPLICATION_CREDENTIALS` at it:

```bash
docker run -d -p 8090:8090 \
  -e GOOGLE_APPLICATION_CREDENTIALS=/app/service-account.json \
  -v /path/to/service-account.json:/app/service-account.json \
  --name optics-mcp-prod optics-mcp-prod
```

!!! warning "Never bake credentials into an image"
    Mount the service account file at run time. A `COPY` of it into the image
    leaves the key in a layer that travels with every copy of that image.

## Reaching Appium from inside a container

An Appium server running on the host is not on `localhost` from the
container's point of view. Use `host.docker.internal` instead — in a project's
`config.yaml`:

```yaml
driver_sources:
  - appium:
      enabled: true
      url: "http://host.docker.internal:4723"
```

or in an MCP `start_session` call:

```json
{
  "driver": "appium",
  "url": "http://host.docker.internal:4723",
  "capabilities": { "...": "..." }
}
```

Docker Desktop resolves that name for you. On Linux it does not exist unless
you add it, and none of the Compose services declares it — so pass it to
`docker run`:

```bash
docker run -d -p 8000:8000 \
  --add-host=host.docker.internal:host-gateway \
  --name optics-api-prod optics-api-prod
```

or layer it onto Compose with a second file:

```yaml
# Docker/docker-compose.host.yml
services:
  app:
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

```bash
docker compose -f Docker/docker-compose.yml -f Docker/docker-compose.host.yml up app
```

## Connecting a client

The REST API answers on `http://<host>:8000`, with `/health` for liveness (the
Compose healthcheck uses it) and FastAPI's generated OpenAPI browser at
`/docs`. See [REST API Usage](REST_API_usage.md).

!!! warning "The API is unauthenticated"
    Neither server ships authentication, and both bind to `0.0.0.0` inside the
    container. Publish their ports only on a trusted network, or put a
    reverse proxy that authenticates in front.

A containerized MCP server always speaks **HTTP transport** — `stdio` is for
local clients that spawn the process themselves. Point your client at the
container:

```json
{
  "mcpServers": {
    "optics": { "url": "http://127.0.0.1:8090/mcp" }
  }
}
```

Use port **8091** for the `mcp-dev` Compose service. See
[MCP Usage](mcp_usage.md).

!!! note "Sessions are not shared"
    `optics serve` and `optics mcp` are separate processes with separate
    in-memory session managers, whether or not they run in the same Compose
    project. A session started against one is invisible to the other, and
    restarting a container clears its sessions.

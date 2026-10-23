# Docker deployment

The deployment guide now lives in the documentation site:

**<https://mozarkai.github.io/optics-framework/usage/docker_deployment/>**

It covers building and running all four images, Compose, vision-backend build
arguments, Google Vision credentials, and reaching Appium from inside a
container. The source page is
[`docs/usage/docker_deployment.md`](../docs/usage/docker_deployment.md) — edit
it there so the site and this directory cannot drift apart.

This directory holds the build inputs themselves:

| Path | Builds |
|---|---|
| `prod/Dockerfile` | REST API (`optics serve`), from PyPI |
| `dev/Dockerfile` | REST API, from a wheel in `dist/` |
| `mcp/prod/Dockerfile` | MCP server (`optics mcp --transport http`), from PyPI |
| `mcp/dev/Dockerfile` | MCP server, from a wheel in `dist/` |
| `docker-compose.yml` | One service per image, on distinct host ports |

All of them must be built from the repository root, because they copy
`poetry.lock` and `scripts/lock_pins.py`:

```sh
docker build -f Docker/prod/Dockerfile -t optics-api-prod .
docker compose -f Docker/docker-compose.yml up --build mcp
```

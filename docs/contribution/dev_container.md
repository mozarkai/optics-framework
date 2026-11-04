# Dev Container and Codespaces

The repository ships a
[`.devcontainer/devcontainer.json`](https://github.com/mozarkai/optics-framework/blob/main/.devcontainer/devcontainer.json)
that builds a ready-to-work environment: Python 3.12, Poetry, the project's
dependencies, the pre-commit hooks, and the editor extensions the project
lints with. Use it instead of the manual setup in the
[Developer Guide](developer_guide.md) if you would rather not install a
toolchain on your machine, or if you want a throwaway environment for a
one-off contribution.

## Opening it

**GitHub Codespaces** — on the repository page, **Code → Codespaces → Create
codespace**. Nothing to install locally.

**VS Code, locally** — install the
[Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)
extension and Docker, clone the repository, then run **Dev Containers: Reopen
in Container** from the command palette.

Either way the first start runs the container's setup command, which installs
the system libraries the vision engines need (`libgl1`, `libglib2.0-0`), a
headless JRE, the Poetry environment (`poetry install`, which includes the
`dev`, `test` and `docs` groups), and the pre-commit hooks. It takes a few
minutes; afterwards the usual commands work without a prefix beyond `poetry
run`:

```bash
poetry run pytest
poetry run ruff check --fix .
poetry run pre-commit run --all-files
poetry run mkdocs serve
```

## What you get

| | |
|---|---|
| **Base image** | `mcr.microsoft.com/devcontainers/python:1-3.12-bullseye` |
| **Tooling** | Poetry, git, Docker CLI (talking to the host daemon), scancode-toolkit |
| **Editor** | Pylance, Ruff, Pylint, markdownlint, YAML, Docker, Conventional Commits |
| **Testing** | pytest enabled as the VS Code test runner |

Docker is wired as *docker-outside-of-docker*, so `docker build` and
`docker compose` inside the container drive the host's daemon — the images in
[Docker Deployment](../usage/docker_deployment.md) build from here as they
would from a normal shell.

## What it does not give you

The container has no Android SDK, no emulator, and no Appium server, so it is
a **development** environment rather than a test-execution one. To run a suite
against a real target from inside it, point `config.yaml` at an Appium server
reachable over the network. For one on the host machine, the container is
started with a `host.docker.internal` host entry, so:

```yaml
driver_sources:
  - appium:
      enabled: true
      url: "http://host.docker.internal:4723"
```

A Codespace has no route to your local machine at all — use a remote Appium
grid, or work on code, docs and unit tests there and run device suites
locally.

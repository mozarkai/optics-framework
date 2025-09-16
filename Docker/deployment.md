#  Optics Framework API Deployment Guide


This guide outlines how to deploy the Optics API using Docker in two modes:

1. **Production**: Uses the published `optics-framework` from PyPI (see `prod/Dockerfile`)
2. **Development**: Uses a locally built `.whl` package from the Poetry-managed `optics-framework` source (see `dev/Dockerfile`)

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
docker build -t optics-api-prod .
```

### Run
```sh
docker run -d -p 8000:8000 --name optics-api-prod optics-api-prod
```

#### Vision Backend Selection
The production Dockerfile supports multiple vision backends via the `VISION_BACKEND` build argument (default: `easyocr`). Supported values:

- `easyocr` (default)
- `google-vision`
- `pytesseract`

Example (Google Vision):
```sh
docker build --build-arg VISION_BACKEND=google-vision -t optics-api-prod .
```

If using Google Vision, mount your service account JSON and set the env variable:
```sh
docker run -d -p 8000:8000 \
  -e GOOGLE_APPLICATION_CREDENTIALS=/app/service-account.json \
  -v /path/to/service-account.json:/app/service-account.json \
  --name optics-api-prod optics-api-prod
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
```sh
cd dev/
docker build \
  --build-arg WHL_FILE=optics_framework-0.x.x-py3-none-any.whl \
  -t optics-api-dev .
```

### Run
```sh
docker run -d -p 8000:8000 --name optics-api-dev optics-api-dev
```

#### Vision Backend Selection
Same as production: use `--build-arg VISION_BACKEND=...` to select the backend.

#### Appium Localhost Note
If running Appium on your host machine, use this URL in your config:

```
appium_url: "http://host.docker.internal:4723"
```

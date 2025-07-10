#  Optics Framework API Deployment Guide

This guide outlines how to deploy the Optics API in two modes:

1. **Production**: Uses the published `optics-framework` from PyPI
2. **Development**: Uses a locally built `.whl` package from the Poetry-managed `optics-framework` source

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
prod/\
├── Dockerfile\
└── requirements.txt
```

### Build

```
cd prod/
docker build -t optics-api-prod .
```

### Run

```
docker run -d -p 8000:8000 --name optics-api-prod optics-api-prod
```

## ✅ Deployment Mode 2: Development (Local .whl)

### 📁 Folder Structure
```
prod/\
├── Dockerfile\
└── requirements.txt
├── dist/
│   └── optics_framework-0.x.x-py3-none-any.whl
```

### Built the .whl package
```
cd /path/to/optics-framework
poetry build
```
### Copy the built .whl package into /dev/dist/:
```
cp dist/*.whl /path/to/optics-docker/dev/dist/
```

### Build (with dynamic .whl path)
```
cd dev/
docker build \
  --build-arg WHL_FILE=optics_framework-0.x.x-py3-none-any.whl \
  -t optics-api-dev .
```

### Run
```
docker run -d -p 8000:8000 --name optics-api-dev optics-api-dev
```

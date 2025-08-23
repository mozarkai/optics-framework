# Mock Remote OCR — quick start

This folder contains a production-friendly FastAPI mock OCR service and Docker artifacts.

Quick steps (from repository root)

- Build CPU image and run:

```bash
docker build -f tools/mock_remote_ocr/scalable/Dockerfile -t optics/mock-ocr:latest .
docker run --rm -p 8090:8090 --name mock-ocr optics/mock-ocr:latest
```

- Build GPU image and run (host must support NVIDIA runtimes):

```bash
docker build -f tools/mock_remote_ocr/scalable/Dockerfile.cuda -t optics/mock-ocr:cuda .
docker run --rm --gpus all -p 8090:8090 --name mock-ocr-cuda optics/mock-ocr:cuda
```

- Or use Docker Compose for local scaling:

```bash
docker compose -f tools/mock_remote_ocr/scalable/docker-compose.yml up --build
```

Health & info

- Liveness/readiness: GET http://127.0.0.1:8090/health
- Server info:  GET http://127.0.0.1:8090/info

Smoke test (detect text)

1) Prepare a small base64 image payload (example uses the repo sample):
assumption is that you are present in base path of this repo

```bash
IMG_B64=$(base64 -w0 assets/sample_text_image.png)
```

2) POST to /detect-text:

```bash
curl -sS -X POST http://127.0.0.1:8090/detect-text \
  -H 'Content-Type: application/json' \
  -d "{\"method\":\"easyocr\",\"language\":\"en\",\"image\":\"${IMG_B64}\"}"
```

Important environment variables

- MOCK_OCR_FORCE_GPU — set to a truthy value to prefer GPU when available.
- UVICORN_WORKERS — number of Uvicorn/Gunicorn workers to run.
- GUNICORN_TIMEOUT — worker timeout in seconds.
- APP_MODULE — ASGI app path (default: `tools/mock_remote_ocr.mock_remote_ocr:app`).

Notes

- The container runs as non-root user `optics` and exposes port 8090 by default.
- EasyOCR models are pre-downloaded into the image to avoid runtime downloads and OOMs; image size is larger as a result.

Troubleshooting

- If /health returns degraded, check container logs for background model initialization messages.
- On architecture-specific apt issues (e.g. arm64), try a different python base image variant in the Dockerfile.

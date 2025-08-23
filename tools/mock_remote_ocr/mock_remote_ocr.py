"""
Mock Remote OCR server compatible with optics_framework.engines.vision_models.ocr_models.remote_ocr.RemoteOCR

Run with:

    python tools/mock_remote_ocr.py

This starts a FastAPI server (uvicorn) on port 8080 with a single endpoint POST /detect-text
that accepts JSON: {"method":"easyocr","image":"<base64>", "language":"en"}

The server decodes the image, measures its dimensions and returns a single mocked detection
covering the whole image. This is intended for local testing without real OCR engines.
"""

from typing import Optional, List, Dict, Any
import binascii
import os
import base64
import io
import sys
import asyncio
import logging
from PIL import Image
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Optional heavy dependencies. We prefer lazy imports so the server can run even
# when they are missing; endpoints will return 501 if easyocr isn't installed.
# Mark heavy optional dependencies as Any so static analysis doesn't assume attributes
easyocr: Any = None
np: Any = None
cv2: Any = None
try:
    import numpy as _np

    np = _np
except ImportError:
    np = None

try:
    import cv2 as _cv2

    cv2 = _cv2
except ImportError:
    cv2 = None

logger = logging.getLogger("mock_remote_ocr")
logging.basicConfig(level=logging.INFO)


class DetectRequest(BaseModel):
    method: Optional[str] = "easyocr"
    image: str
    language: Optional[str] = "en"


class MockRemoteOCR:
    """Object-oriented wrapper around the mock OCR FastAPI app.

    All functionality lives behind this class; instantiate and then use
    the `app` attribute for uvicorn or ASGI servers.
    """

    class HealthResponse(JSONResponse):
        """Small helper response for health checks.

        Usage: return HealthResponse(reader_initialized=True)
        This returns JSON body and sets status code 200 when initialized, 503 otherwise.
        """

        def __init__(self, reader_initialized: bool):
            body = (
                {"status": "ok", "reader_initialized": True}
                if reader_initialized
                else {"status": "degraded", "reader_initialized": False}
            )
            status_code = 200 if reader_initialized else 503
            super().__init__(content=body, status_code=status_code)

    def __init__(self, base_path: Optional[str] = None):
        self.api_app = FastAPI(title="Mock Remote OCR")

        # allow override from env or parameter
        if base_path is None:
            base_path = os.getenv("MOCK_OCR_BASE_PATH", "/")
        if not base_path.startswith("/"):
            base_path = "/" + base_path
        if base_path != "/" and base_path.endswith("/"):
            base_path = base_path[:-1]

        if base_path == "/":
            self.app = self.api_app
        else:
            self.app = FastAPI(title="Mock Remote OCR Gateway")
            self.app.mount(base_path, self.api_app)

        # register routes on api_app using closures that delegate to methods
        self.api_app.add_api_route(
            "/detect-text", self._make_detect_text(), methods=["POST"]
        )
        self.api_app.add_api_route("/info", self._make_info(), methods=["GET"])
        self.api_app.add_api_route("/health", self._make_health(), methods=["GET"])
        self.api_app.add_api_route("/", self._make_root(), methods=["GET"])

        # Schedule background reader initialization on application startup.
        # We do this in a background task so startup isn't blocked by model
        # downloads or heavy initialization, but /info will reflect the
        # initialized state once complete.

        async def _lazy_and_init(language: str) -> None:
            try:
                # lazy import may raise HTTPException if easyocr is missing;
                # convert this to a logged error instead.
                self._lazy_import_easyocr()
            except Exception:
                logger.exception("easyocr not available during startup")
                return

            try:
                await self._init_reader_async(language)
                logger.info("EasyOCR reader initialized in background")
            except Exception:
                logger.exception("Failed to initialize easyocr.Reader in background")

        async def _startup_initialize_reader() -> None:
            try:
                # prefer english by default; errors are logged but won't crash
                # the application startup.
                await _lazy_and_init("en")
            except Exception:
                logger.exception("Background reader initialization failed")

        def _startup_schedule_reader() -> None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_startup_initialize_reader())
            except RuntimeError:
                # no running loop; ignore — startup handler should always be
                # invoked in the running event loop, but guard just in case.
                logger.debug("No running loop to schedule reader init")

        # attach handlers to api_app
        self.api_app.add_event_handler("startup", _startup_schedule_reader)

    # ---- helpers now as instance methods ----
    def _decode_image_bytes(self, img_bytes: bytes) -> Any:
        if cv2 is not None and np is not None:
            _decode_exc_types = (ValueError, TypeError, OSError)
            if hasattr(cv2, "error"):
                _decode_exc_types = _decode_exc_types + (cv2.error,)
            try:
                arr = np.frombuffer(img_bytes, np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                return img
            except _decode_exc_types:
                logger.debug(
                    "cv2-based decode failed, falling back to PIL", exc_info=True
                )

        try:
            pil_im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            return np.array(pil_im) if np is not None else pil_im
        except (OSError, ValueError):
            logger.debug("PIL-based decode failed", exc_info=True)
            return None

    def _lazy_import_easyocr(self) -> None:
        global easyocr
        if easyocr is None:
            try:
                import easyocr as _easyocr

                easyocr = _easyocr
            except ImportError as exc:
                raise HTTPException(
                    status_code=501, detail="easyocr is not installed on the server"
                ) from exc

    async def _init_reader_async(self, language: str) -> Any:
        if not hasattr(self.api_app.state, "ocr_lock"):
            self.api_app.state.ocr_lock = asyncio.Lock()

        reader = getattr(self.api_app.state, "ocr_reader", None)
        if reader is not None:
            return reader

        async with self.api_app.state.ocr_lock:
            reader = getattr(self.api_app.state, "ocr_reader", None)
            if reader is not None:
                return reader

            env_force = os.getenv("MOCK_OCR_FORCE_GPU")
            if env_force is not None:
                gpu_flag = env_force.lower() in ("1", "true", "yes", "on")
            else:
                try:
                    import torch

                    gpu_flag = torch.cuda.is_available()
                except ImportError:
                    gpu_flag = False

            logger.info("Initializing easyocr.Reader (gpu=%s)", gpu_flag)

            def _init():
                return easyocr.Reader([language], gpu=gpu_flag)

            try:
                reader = await asyncio.to_thread(_init)
            except Exception as e:
                raise HTTPException(
                    status_code=500, detail=f"Failed to initialize easyocr: {e}"
                ) from e

            self.api_app.state.ocr_reader = reader
            return reader

    async def _run_readtext_async(self, reader: Any, img: Any) -> Any:
        try:
            if (
                cv2 is not None
                and np is not None
                and isinstance(img, np.ndarray)
                and img.ndim == 3
                and img.shape[-1] == 3
            ):
                img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        except (AttributeError, TypeError, ValueError):
            logger.debug(
                "Color conversion failed, continuing with original image", exc_info=True
            )

        return await asyncio.to_thread(reader.readtext, img)

    def _process_raw_results(self, raw_results: Any) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for item in raw_results:
            try:
                bbox_raw, text, confidence = item
                bbox: List[List[int]] = []
                for pt in bbox_raw:
                    try:
                        x = int(float(pt[0]))
                        y = int(float(pt[1]))
                        bbox.append([x, y])
                    except (TypeError, ValueError, IndexError):
                        continue
                if len(bbox) >= 4:
                    results.append(
                        {"bbox": bbox, "text": text, "confidence": float(confidence)}
                    )
            except (TypeError, ValueError, IndexError):
                logger.debug("Skipping malformed OCR item", exc_info=True)
                continue

        return results

    def _get_image_bytes(self, image_b64: Optional[str]) -> bytes:
        if not image_b64:
            raise HTTPException(status_code=400, detail="Missing image payload")
        try:
            return base64.b64decode(image_b64)
        except (binascii.Error, TypeError) as exc:
            raise HTTPException(status_code=400, detail="Invalid base64 image") from exc

    # ---- route factories: return async functions that call instance methods ----
    def _make_detect_text(self):
        async def detect_text(req: DetectRequest) -> Dict[str, List[Dict[str, Any]]]:
            img_bytes = self._get_image_bytes(req.image)
            img = await asyncio.to_thread(self._decode_image_bytes, img_bytes)
            if img is None:
                return {"results": []}

            self._lazy_import_easyocr()
            reader = getattr(self.api_app.state, "ocr_reader", None)
            if reader is None:
                reader = await self._init_reader_async(req.language or "en")

            try:
                raw_results = await self._run_readtext_async(reader, img)
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"EasyOCR failed: {e}")

            results = self._process_raw_results(raw_results)
            return {"results": results}

        return detect_text

    def _make_info(self):
        def info() -> Dict[str, Any]:
            env_force = os.getenv("MOCK_OCR_FORCE_GPU")
            if env_force is not None:
                gpu_flag = env_force.lower() in ("1", "true", "yes", "on")
                source = "env"
            else:
                try:
                    import torch

                    gpu_flag = torch.cuda.is_available()
                    source = "torch"
                except ImportError:
                    gpu_flag = False
                    source = "none"

            reader = getattr(self.api_app.state, "ocr_reader", None)
            return {
                "gpu_enabled": bool(gpu_flag),
                "gpu_decision_source": source,
                "reader_initialized": reader is not None,
            }

        return info

    def _make_health(self):
        def health() -> "MockRemoteOCR.HealthResponse":
            reader = getattr(self.api_app.state, "ocr_reader", None)
            return MockRemoteOCR.HealthResponse(reader_initialized=(reader is not None))

        return health

    def _make_root(self):
        def root() -> "MockRemoteOCR.HealthResponse":
            # call the health factory function and return its result
            return self._make_health()()

        return root


# expose an app instance for uvicorn to import
_instance = MockRemoteOCR()
app = _instance.app

if __name__ == "__main__":
    # Run with uvicorn when executed directly
    try:
        import uvicorn
    except Exception:
        print(
            "uvicorn is required to run the mock server. Install with: pip install uvicorn[standard]"
        )
        sys.exit(1)

    host = os.getenv("MOCK_OCR_HOST", "127.0.0.1")
    port = int(os.getenv("MOCK_OCR_PORT", "8090"))
    # Use __file__ to ensure correct module path resolution
    uvicorn.run(app, host=host, port=port, log_level="debug")

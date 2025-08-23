"""Mock Remote OCR server compatible with optics_framework.engines.vision_models.ocr_models.remote_ocr.RemoteOCR

Run with:

    python tools/mock_remote_ocr.py

This starts a FastAPI server (uvicorn) on port 8080 with a single endpoint POST /detect-text
that accepts JSON: {"method":"easyocr","image":"<base64>", "language":"en"}

The server decodes the image, measures its dimensions and returns a single mocked detection
covering the whole image. This is intended for local testing without real OCR engines.
"""
from typing import Optional, List, Dict, Any
import os
import base64
import io
import sys
from PIL import Image
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Lazy import for easyocr to avoid heavy import at module load if not used
try:
    import easyocr
except Exception:
    easyocr = None

try:
    import numpy as np
    import cv2
except Exception:
    np = None
    cv2 = None

app = FastAPI(title="Mock Remote OCR")

class DetectRequest(BaseModel):
    method: Optional[str] = "easyocr"
    image: str
    language: Optional[str] = "en"

@app.post("/detect-text")
async def detect_text(req: DetectRequest) -> Dict[str, List[Dict[str, Any]]]:
    """Return a mocked detection result compatible with remote_ocr.RemoteOCR.detect_text.

    Response format:
    {"results": [{"bbox": [[x1,y1],[x2,y2],[x3,y3],[x4,y4]], "text": "...", "confidence": 0.95}, ...]}
    """
    if not req.image:
        raise HTTPException(status_code=400, detail="Missing image payload")

    # Decode base64 image bytes
    try:
        img_bytes = base64.b64decode(req.image)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 image")

    # Convert to numpy array (preferred) or PIL image
    img = None
    if cv2 is not None and np is not None:
        try:
            arr = np.frombuffer(img_bytes, np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except Exception:
            img = None

    if img is None:
        # Fallback to PIL -> numpy
        try:
            pil_im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            img = np.array(pil_im) if np is not None else None
        except Exception:
            img = None

    if img is None:
        # Could not decode image
        return {"results": []}

    # If easyocr not installed, return explicit error
    if easyocr is None:
        raise HTTPException(status_code=501, detail="easyocr is not installed on the server")

    # Lazy initialize reader on the app object to reuse across requests
    reader = getattr(app.state, "ocr_reader", None)
    if reader is None:
        try:
            reader = easyocr.Reader([req.language or "en"], gpu=False)
            app.state.ocr_reader = reader
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to initialize easyocr: {e}")

    try:
        # easyocr expects images in RGB or grayscale numpy arrays
        raw_results = reader.readtext(img)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"EasyOCR failed: {e}")

    results: List[Dict[str, Any]] = []
    for item in raw_results:
        # item is (bbox, text, confidence)
        try:
            bbox_raw = item[0]
            text = item[1]
            confidence = float(item[2])
            # Normalize bbox to list of [int,int]
            bbox = []
            for pt in bbox_raw:
                try:
                    x = int(float(pt[0]))
                    y = int(float(pt[1]))
                    bbox.append([x, y])
                except (TypeError, ValueError, IndexError) as ex:
                    # skip malformed point but continue with other points
                    print(f"Skipping malformed bbox point {pt}: {ex}")
                    continue
            if len(bbox) >= 4:
                results.append({"bbox": bbox, "text": text, "confidence": confidence})
        except (TypeError, ValueError, IndexError) as ex:
            # skip malformed items
            print(f"Skipping malformed OCR item: {ex}")
            continue

    return {"results": results}

if __name__ == "__main__":
    # Run with uvicorn when executed directly
    try:
        import uvicorn
    except Exception:
        print("uvicorn is required to run the mock server. Install with: pip install uvicorn[standard]")
        sys.exit(1)

    host = os.getenv("MOCK_OCR_HOST", "127.0.0.1")
    port = int(os.getenv("MOCK_OCR_PORT", "8090"))
    uvicorn.run("tools.mock_remote_ocr:app", host=host, port=port, log_level="debug")

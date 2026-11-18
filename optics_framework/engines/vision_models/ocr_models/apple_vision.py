import cv2
import numpy as np
import objc
import Foundation
import Vision
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.text_interface import TextInterface

RECOGNITION_LEVELS = {
    "accurate": Vision.VNRequestTextRecognitionLevelAccurate,
    "fast": Vision.VNRequestTextRecognitionLevelFast,
}


def _box(observation, width: int, height: int) -> list[tuple[int, int]]:
    # Vision measures from the bottom-left corner, in fractions of the frame.
    (x, y), (w, h) = observation.boundingBox()
    left, right = int(x * width), int((x + w) * width)
    top, bottom = int((1 - y - h) * height), int((1 - y) * height)
    return [(left, top), (right, top), (right, bottom), (left, bottom)]


class AppleVisionHelper(TextInterface):
    """OCR with the macOS Vision framework."""

    def __init__(self, config=None):
        """``capabilities.recognition_level`` is ``accurate`` (default) or ``fast``; ``capabilities.languages`` defaults to ``["en-US"]``."""
        caps = (config or {}).get("capabilities") or {}
        level = caps.get("recognition_level", "accurate")
        if level not in RECOGNITION_LEVELS:
            raise OpticsError(
                Code.E0501, message=f"apple_vision: recognition_level must be one of {sorted(RECOGNITION_LEVELS)}, got {level!r}."
            )
        self._level = RECOGNITION_LEVELS[level]
        self._languages = list(caps.get("languages") or ["en-US"])

    def detect_text(self, input_data: np.ndarray):
        ok, encoded = cv2.imencode(".bmp", input_data)
        if not ok:
            raise ValueError("apple_vision: the frame could not be encoded.")
        height, width = input_data.shape[:2]
        with objc.autorelease_pool():
            data = Foundation.NSData.dataWithBytes_length_(encoded.tobytes(), len(encoded))
            request = Vision.VNRecognizeTextRequest.alloc().init()
            request.setRecognitionLevel_(self._level)
            request.setRecognitionLanguages_(self._languages)
            request.setUsesLanguageCorrection_(True)
            handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
            done, error = handler.performRequests_error_([request], None)
            if not done:
                raise ValueError(f"apple_vision could not read the frame: {error}")
            results = []
            for observation in request.results() or []:
                candidate = observation.topCandidates_(1)[0]
                results.append((_box(observation, width, height), str(candidate.string()), float(candidate.confidence())))
        return " ".join(text for _, text, _ in results), results

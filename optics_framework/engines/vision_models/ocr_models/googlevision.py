from optics_framework.common.error import Code, OpticsError
from optics_framework.common.google_credentials import service_account_credentials
from optics_framework.common.text_interface import TextInterface
import cv2
import numpy as np
from google.api_core.client_options import ClientOptions
from google.cloud import vision
from google.cloud.vision_v1 import ImageAnnotatorClient


class GoogleVisionHelper(TextInterface):
    """
    Helper class for Optical Character Recognition (OCR) using EasyOCR.

    This class uses EasyOCR to detect text in images and optionally locate
    specific reference text.
    """

    def __init__(self, config=None):
        """Use ``capabilities.api_key`` or the service account file in ``capabilities.credentials_path``, else Application Default Credentials."""
        caps = (config or {}).get("capabilities") or {}
        api_key, credentials_path = caps.get("api_key"), caps.get("credentials_path")
        if api_key and credentials_path:
            raise OpticsError(Code.E0501, message="google-vision: set capabilities.api_key or credentials_path, not both.")
        self._client: ImageAnnotatorClient | None = None
        if api_key:
            self._client = ImageAnnotatorClient(client_options=ClientOptions(api_key=api_key))
        elif credentials_path:
            self._client = ImageAnnotatorClient(credentials=service_account_credentials(credentials_path))

    def _annotator(self) -> ImageAnnotatorClient:
        if self._client is None:
            self._client = ImageAnnotatorClient()
        return self._client

    def detect_text(self,frame: np.ndarray):
        """
        Detects text in a given NumPy array using Google Vision API and returns standardized OCR format.

        Returns:
            Tuple[None, List[Tuple[bbox, text, confidence]]]
            bbox = List[Tuple[int, int]] with 4 points
        """
        if frame is None or not isinstance(frame, np.ndarray):
            raise ValueError("Invalid frame provided. Ensure it's a valid NumPy array.")

        _, encoded_image = cv2.imencode('.jpg', frame)
        image_bytes = encoded_image.tobytes()

        image = vision.Image(content=image_bytes)

        response = self._annotator().text_detection(image=image)
        texts = response.text_annotations

        results = []
        for text in texts[1:]:  # Skip full block (index 0)
            text_str = text.description
            vertices = text.bounding_poly.vertices
            if len(vertices) >= 4:
                bbox = [(v.x, v.y) for v in vertices]
                results.append((bbox, text_str, None)) # None for confidence placeholder as it's not provided by Google Vision API
            else:
                continue

        return None, results

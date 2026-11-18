import numpy as np
from rapidocr import RapidOCR
from optics_framework.common.text_interface import TextInterface


class RapidOCRHelper(TextInterface):
    """OCR with RapidOCR on onnxruntime, using the models its wheel ships."""

    def __init__(self, config=None):
        self._engine = RapidOCR(params={"Global.log_level": "error"})

    def detect_text(self, input_data: np.ndarray):
        output = self._engine(input_data)
        if output.boxes is None:
            return "", []
        results = [
            ([(int(x), int(y)) for x, y in box], str(text), float(score))
            for box, text, score in zip(output.boxes, output.txts, output.scores)
        ]
        return " ".join(text for _, text, _ in results), results

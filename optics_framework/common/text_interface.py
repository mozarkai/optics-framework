from abc import ABC, abstractmethod
from typing import Optional, Tuple, List

from optics_framework.common.text_match import find_text


class TextInterface(ABC):
    """
    Abstract base class for text processing engines.

    This interface defines methods for detecting and locating text (e.g., via OCR)
    within input data, such as images or video frames, implementing the
    :class:`TextInterface`.

    Implementers should handle specific input types (e.g., image bytes, file paths)
    and reference data as needed.
    """

    def find_element(
        self, input_data, text, index=None, strict=False
    ) -> Optional[Tuple[bool, Tuple[int, int], Tuple[Tuple[int, int], Tuple[int, int]]]]:
        """Return ``(True, center, (top_left, bottom_right))`` for the ``index``-th match of ``text``, else None."""
        detected = self.detect_text(input_data)
        matches = find_text(detected[1] if detected else [], text, strict)
        position = int(index or 0)
        if not 0 <= position < len(matches):
            return None
        center, box = matches[position]
        return True, center, box

    @abstractmethod
    def detect_text(
        self, input_data
    ) -> Optional[Tuple[str, List[Tuple[List[Tuple[int, int]], str, float]]]]:
        """
        Detect text in the input data and return a tuple of (detected_text_str, results).

        Each result is a tuple: (bbox, text, confidence) where bbox is a list of (x,y) tuples.

        :param input_data: The input source (e.g., image, video frame) for detection.
        :type input_data: Any
        """
        pass

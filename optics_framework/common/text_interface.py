import hashlib
import threading
from abc import ABC, abstractmethod
from collections import OrderedDict
from functools import wraps
from typing import Any, Optional, Tuple, List

import numpy as np

from optics_framework.common.text_match import find_text

FRAMES_REMEMBERED = 4
_reads_lock = threading.Lock()


def _frame_key(frame: Any) -> bytes | None:
    if not isinstance(frame, np.ndarray):
        return None
    digest = hashlib.blake2b(np.ascontiguousarray(frame).data, digest_size=16)
    digest.update(f"{frame.shape}{frame.dtype.str}".encode())
    return digest.digest()


def _once_per_frame(detect_text):
    @wraps(detect_text)
    def read(self, input_data, *args, **kwargs):
        key = None if args or kwargs else _frame_key(input_data)
        if key is None:
            return detect_text(self, input_data, *args, **kwargs)
        with _reads_lock:
            reads = self.__dict__.setdefault("_frame_reads", OrderedDict())
            if key in reads:
                reads.move_to_end(key)
                return reads[key]
        result = detect_text(self, input_data)
        with _reads_lock:
            reads[key] = result
            while len(reads) > FRAMES_REMEMBERED:
                reads.popitem(last=False)
        return result
    return read


class TextInterface(ABC):
    """
    Abstract base class for text processing engines.

    This interface defines methods for detecting and locating text (e.g., via OCR)
    within input data, such as images or video frames, implementing the
    :class:`TextInterface`.

    Implementers should handle specific input types (e.g., image bytes, file paths)
    and reference data as needed.

    A subclass's ``detect_text`` reads a frame once: a repeated call with the same
    pixels returns the earlier result.
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if "detect_text" in cls.__dict__:
            cls.detect_text = _once_per_frame(cls.__dict__["detect_text"])

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

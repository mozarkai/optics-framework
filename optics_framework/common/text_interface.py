from abc import ABC, abstractmethod
from typing import Optional, Tuple, Any, List


class TextInterface(ABC):
    """
    Abstract base class for text processing engines.

    This interface defines methods for detecting and locating text (e.g., via OCR)
    within input data, such as images or video frames, implementing the
    :class:`TextInterface`.

    Implementers should handle specific input types (e.g., image bytes, file paths)
    and reference data as needed.
    """

    @abstractmethod
    def element_exist(
        self, input_data: Any, reference_data: Any
    ) -> Optional[Tuple[int, int]]:
        """
        Find the location of a reference image within the input data.

        :param input_data: The input source (e.g., image, video frame) for detection.
        :type input_data: Any
        :param reference_data: The reference data used for matching or comparison.
        :type reference_data: Any
        :return: A tuple (x, y) representing the top-left corner of the reference image,
                 or None if not found.
        :rtype: Optional[Tuple[int, int]]
        """
        pass

    @abstractmethod
    def find_element(
        self, input_data, text, index=None
    ) -> Optional[
        Tuple[bool, Tuple[int, int], Tuple[Tuple[int, int], Tuple[int, int]]]
    ]:
        """
        Locate specific text in the input data using OCR and return detailed detection info.

        :param input_data: The input source (e.g., image, video frame) for detection.
        :type input_data: Any
        :param text: The text to locate in the input data.
        :type text: str
        :return: A tuple (found, center, bounds) where:
                 - found: bool indicating if the text was found
                 - center: (x, y) coordinates of the text center
                 - bounds: ((x1, y1), (x2, y2)) bounding box (top-left, bottom-right)
                 Returns None if not found.
        :rtype: Optional[Tuple[bool, Tuple[int, int], Tuple[Tuple[int, int], Tuple[int, int]]]]
        """
        pass

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

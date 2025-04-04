from abc import ABC
from typing import Optional, Tuple


class ImageInterface(ABC):
    """
    Abstract base class for vision-based detection models.

    This interface enforces the implementation of a :meth:`detect` method,
    which processes input data to identify specific objects, patterns, or text.
    """


    def element_exist(self, input_data, reference_data) -> Optional[Tuple[int, int]]:
        """
        Find the location of a reference image within the input data.

        :param input_data: The input source (e.g., image, video frame) for detection.
        :type input_data: Any
        :param reference_data: The reference data used for matching or comparison.
        :type reference_data: Any
        :return: A tuple ``(x, y)`` representing the top-left corner of the reference image,
                 or ``None`` if the image is not found.
        :rtype: Optional[Tuple[int, int]]
        """
        pass

    def locate(self, input_data, image, index=None) -> Optional[Tuple[int, int]]:
        """
        Find the location of text within the input data.

        :param input_data: The input source (e.g., image, video frame) for detection.
        :type
        input_data: Any
        :param text: The text to search for.
        :type text: str
        :return: A tuple ``(x, y)`` representing the centre of the text,
                 or ``None`` if the text is not found.
        :rtype: Optional[Tuple[int, int]]
        """
        pass

    def find_element(self, input_data, image, index=None) -> Optional[Tuple[bool, Tuple[int, int], Tuple[Tuple[int, int], Tuple[int, int]]]]:
        """
        Find the location of an image within the input data.

        :param input_data: The input source (e.g., image, video frame) for detection.
        :type input_data: Any
        :param image: The image to search for.
        :type image: Any
        :return: A tuple containing a boolean indicating whether the image was found,
                 the center coordinates of the image, and the bounding box coordinates.
        :rtype: Optional[Tuple[bool, Tuple[int, int], Tuple[Tuple[int, int], Tuple[int, int]]]]
        """
        pass

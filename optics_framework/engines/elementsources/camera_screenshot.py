"""
Capture Camera Module

This module provides a concrete implementation of `ScreenshotInterface`
that captures images from a webcam.
"""

import cv2
import numpy as np
from typing import Optional
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.logging_config import logger

class CameraScreenshot(ElementSourceInterface):
    """
    Capture screenshots using a webcam.
    """

    def __init__(self, camera_index: int = 0):
        """
        Initialize the camera capture.

        Args:
            camera_index (int): Index of the camera device (default: 0).
        """
        self.camera_index = camera_index
        self.cap = cv2.VideoCapture(self.camera_index)

    def capture(self) -> Optional[np.ndarray]:
        """
        Capture an image from the webcam.

        Returns:
            Optional[np.ndarray]: The captured image as a NumPy array, or `None` on failure.
        """
        if not self.cap.isOpened():
            return None

        ret, frame = self.cap.read()
        if ret:
            return frame
        return None

    def __del__(self):
        """Release the camera when the object is destroyed."""
        if self.cap.isOpened():
            self.cap.release()

    def locate(self, image: np.ndarray, template: np.ndarray) -> Optional[tuple]:
        logger.exception("CameraScreenshot does not support locating elements.")
        raise NotImplementedError("CameraScreenshot does not support locating elements.")

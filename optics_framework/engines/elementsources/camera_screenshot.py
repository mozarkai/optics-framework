"""
Capture Camera Module

This module provides a concrete implementation of `ScreenshotInterface`
that captures images from a webcam.
"""

import cv2
import numpy as np
import socket
from typing import Optional
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.config_handler import ConfigHandler
from optics_framework.common.logging_config import internal_logger

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
        self.camera_screenshot_config = next((item['camera_screenshot'] for item in ConfigHandler.get_instance().config.elements_sources if 'camera_screenshot' in item),None)
        self.port = self.camera_screenshot_config.capabilities.get('port', 3000) if self.camera_screenshot_config else 3000
        self.ip_address = self.camera_screenshot_config.capabilities.get('ip_address', '127.0.0.1')
        self.sock = self.create_tcp_connection(ip=self.ip_address, port=self.port)
        self.deskew_corners = self.camera_screenshot_config.capabilities.get('deskew_corners', None) if self.camera_screenshot_config else None
        self.out_width = self.camera_screenshot_config.capabilities.get('out_width', 1080) if self.camera_screenshot_config else 1080
        self.out_height = self.camera_screenshot_config.capabilities.get('out_height', 488) if self.camera_screenshot_config else 488

    def capture(self) -> Optional[np.ndarray]:
        """
        Capture an image from the webcam.

        Returns:
            Optional[np.ndarray]: The captured image as a NumPy array, or `None` on failure.
        """
        if self.sock:
            frame = self.take_screenshot()
            return frame
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
        internal_logger.exception("CameraScreenshot does not support locating elements.")
        raise NotImplementedError("CameraScreenshot does not support locating elements.")

    def locate_using_index(self, element, index):
        internal_logger.exception("CameraScreenshot does not support locating elements using index.")
        raise NotImplementedError("CameraScreenshot does not support locating elements using index.")

    def assert_elements(self, elements):
        internal_logger.exception("CameraScreenshot does not support asserting elements.")
        raise NotImplementedError(
            "CameraScreenshot does not support asserting elements.")


    def create_tcp_connection(self, ip, port):
        port = int(port)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((ip, port))
            internal_logger(f"TCP connection established with {ip}:{port}")
            return sock
        except Exception as e:
            internal_logger(f"Error creating TCP connection: {e}")
            return None

    def take_screenshot(self):
        """
        Takes a screenshot using the provided socket and returns the image data as an OpenCV image.
        """
        try:
            if not self.sock:
                raise ConnectionError("No valid socket connection. Unable to take screenshot.")

            # Send the screenshot command
            self.sock.sendall(b'screenshot\n')
            internal_logger("Taking screenshot...")

            # Read the first 8 bytes to get the length of the image data
            length_bytes = self.sock.recv(8)
            if len(length_bytes) < 8:
                raise ValueError("Failed to read the length of the image data.")

            image_length = int.from_bytes(length_bytes, byteorder='little', signed=False)
            internal_logger(f"Expected image data length: {image_length} bytes")

            # Initialize a bytearray for the image data
            image_data = bytearray()
            remaining_bytes = image_length

            while remaining_bytes > 0:
                chunk_size = min(1024, remaining_bytes)
                chunk = self.sock.recv(chunk_size)
                if not chunk:
                    raise ValueError("Socket connection closed prematurely.")
                image_data.extend(chunk)
                remaining_bytes -= len(chunk)
            internal_logger("Transferred image data...")

            # Convert the received image data to a numpy array
            nparr = np.frombuffer(image_data, np.uint8)

            # Decode the image data to an OpenCV image
            screenshot = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            internal_logger("Image ready, setting screenshot")
            self.set_screenshot(screenshot)
            return screenshot
        except Exception as e:
            internal_logger(f"An error occurred: {e}")
            return None


    def deskew_image(self, image):
        """
        Deskews the given image using the provided corner coordinates and output dimensions.

        Args:
            image (numpy.ndarray): The input image as a NumPy array.
            corners (list): The 4 corner coordinates [[x1, y1], [x2, y2], [x3, y3], [x4, y4]].
            output_width (int): The width of the output deskewed image.
            output_height (int): The height of the output deskewed image.

        Returns:
            numpy.ndarray: The deskewed image as a NumPy array.
        """
        try:
            corners = self.deskew_corners
            out_width, out_height = self.out_width, self.out_height
            src_points = np.array([list(map(float, point.split(','))) for point in corners], dtype=np.float32)
            # Define the destination points for the perspective transform
            dst_points = np.array([
                [0, 0],
                [out_width - 1, 0],
                [0, out_height - 1],
                [out_width - 1, out_height - 1]
            ], dtype=np.float32)

            # Compute the perspective transform matrix
            transform_matrix = cv2.getPerspectiveTransform(src_points, dst_points)

            # Perform the perspective warp
            deskewed_image = cv2.warpPerspective(image, transform_matrix, (out_width, out_height))

            return deskewed_image

        except Exception as e:
            raise ValueError(f"Error performing deskew: {e}")


    def rotate(self, img, rotation: str):
        try:

            if rotation.lower == 'clockwise':
                rotated_img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
                internal_logger('Image rotated clockwise.')
            elif rotation.lower == 'counterclockwise':
                rotated_img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
                internal_logger('Image rotated counterclockwise')
            else:
                internal_logger('Rotation argument not defined, returning unrotated image.')
                return img
            return rotated_img
        except Exception as e:
            internal_logger(f'An error occurred while performing rotation: {e}, returning unrotated image.')
            return img

    def take_ext_screenshot(self):
        frame = self.take_screenshot()
        if self.deskew_corners:
            frame = self.deskew_image(frame)
        rotation = str(self.camera_screenshot_config.capabilities.get('rotation', None) if self.camera_screenshot_config else None)
        frame = self.rotate(frame, rotation)
        return frame

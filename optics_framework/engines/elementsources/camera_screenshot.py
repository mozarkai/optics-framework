from itertools import combinations
from typing import Optional, Dict, Any, List, Literal
import os
import socket
from urllib.parse import urlparse
import cv2
import numpy as np
import requests
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.logging_config import internal_logger


ENV_SCREENSHOT_URL = "WEBCAM_SCREENSHOT_URL"
ENV_SCREENSHOT_TOKEN = "WEBCAM_SCREENSHOT_TOKEN"  # nosec B105 - an env var name, not a secret
ENV_SCREENSHOT_TCP_PORT = "WEBCAM_SCREENSHOT_TCP_PORT"
ENV_TCP_FALLBACK_HOST = "127.0.0.1"
HTTP_SCHEMES = frozenset({"http", "https"})
HTTP_TIMEOUT_SECONDS = 15
HTTP_OK = 200
HTTP_ERROR_MESSAGE_LIMIT = 300
ROTATIONS = {"clockwise": cv2.ROTATE_90_CLOCKWISE, "counterclockwise": cv2.ROTATE_90_COUNTERCLOCKWISE}


class CameraScreenshotHttpError(RuntimeError):
    """Raised when the HTTP screenshot endpoint answers with a non-200 status.

    Carries the status plus the ``code`` / ``message`` of the server's JSON error
    body. The bearer token is never part of the message.
    """

    def __init__(self, status: int, code: str | None, message: str | None):
        self.status = status
        self.code = code
        self.server_message = message
        super().__init__(
            f"Camera screenshot request failed: HTTP {status}"
            f" code={code or 'unknown'} message={message or 'none'}"
        )


def corner_points(corners: list[str]) -> np.ndarray:
    """The deskew corners as points; ``ValueError`` unless they are four finite 'x,y' corners of a convex screen, in order."""
    if len(corners) != 4:
        raise ValueError(f"deskew_corners needs 4 points, got {len(corners)}")
    try:
        points = np.array([[float(value) for value in corner.split(",")] for corner in corners], dtype=np.float32)
    except ValueError as exc:
        raise ValueError(f"deskew_corners must be 'x,y' points, got {corners}") from exc
    if points.shape != (4, 2) or not np.isfinite(points).all():
        raise ValueError(f"deskew_corners must be finite 'x,y' points, got {corners}")
    if any(_collinear(*trio) for trio in combinations(points, 3)) or not cv2.isContourConvex(points):
        raise ValueError(f"deskew_corners must be the corners of a convex screen, in order: {corners}")
    return points


def _collinear(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> bool:
    return (b[0] - a[0]) * (c[1] - a[1]) == (b[1] - a[1]) * (c[0] - a[0])


class CapabilitiesConfig(BaseModel):
    camera_index: Optional[int] = Field(None, description="Index of the camera to use")
    url: Optional[str] = Field(
        None,
        description=(
            "Screenshot endpoint: 'ip:port' (raw TCP) or an http(s):// URL "
            "(e.g., 127.0.0.1:3000 or http://127.0.0.1:3000/screenshot)"
        ),
    )
    token: SecretStr | None = Field(
        None,
        description="Bearer token sent to an http(s):// url; masked in repr and dumps",
    )
    out_width: int = Field(1080, gt=0, description="Output width of the screenshot")
    out_height: int = Field(1920, gt=0, description="Output height of the screenshot")
    deskew_corners: Optional[list[str]] = Field(
        None,
        description="The screen's corners in the camera frame as 'x,y': top-left, top-right, bottom-right, bottom-left",
    )
    rotation: Optional[Literal["clockwise", "counterclockwise"]] = Field(
        None, description="Quarter turn applied after deskewing"
    )
    header_size: Literal[4, 8] = Field(
        8, description="Size of the image length header in bytes (4 or 8)"
    )
    byte_order: Literal["little", "big"] = Field(
        "little", description="Byte order for the header (little or big endian)"
    )

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("deskew_corners")
    @classmethod
    def _screen_corners(cls, corners: Optional[list[str]]) -> Optional[list[str]]:
        if corners is not None:
            corner_points(corners)
        return corners

    @field_validator("rotation", mode="before")
    @classmethod
    def _lowercase_rotation(cls, rotation: Any) -> Any:
        return rotation.lower() if isinstance(rotation, str) else rotation

    @property
    def has_camera_index_or_url(self) -> Any:
        return self.camera_index if self.camera_index is not None else self.url


class CameraScreenshot(ElementSourceInterface):
    """
    Capture screenshots using a webcam or TCP connection.
    """

    DEPENDENCY_TYPE = "elements_sources"
    NAME = "camera_screenshot"
    # The frame shows the device screen, so matches map to the window of whichever driver acts on them.
    ACCEPTS_ANY_DRIVER = True

    def __init__(self, config: Optional[Dict[str, Any]] = None, driver: Optional[Any] = None) -> None:
        """
        Initialize the camera capture with either webcam or TCP connection.
        Args:
            config: Optional config dictionary for extensibility.
        """
        if not config:
            internal_logger.error(
                f"No configuration provided for {self.DEPENDENCY_TYPE}: {self.NAME}"
            )
            raise ValueError("Camera screenshot not enabled in config")
        try:
            self.capabilities_model = CapabilitiesConfig(
                **config.get("capabilities", {})
            )
        except ValidationError as ve:
            internal_logger.error(f"Invalid capabilities: {ve}")
            raise
        capabilities: CapabilitiesConfig = self.capabilities_model

        # Initialize configuration variables
        self.driver = driver
        self.camera_index: Optional[int] = capabilities.camera_index
        self.out_width: int = capabilities.out_width
        self.out_height: int = capabilities.out_height
        self.rotation: Optional[str] = capabilities.rotation
        self._deskew = self._deskew_matrix(capabilities)
        self.header_size: int = capabilities.header_size
        self.byte_order: str = capabilities.byte_order

        self.http_url: str | None = None
        self._http_token: str | None = None
        self.sock = None
        self.cap = None
        self.ip_address = None
        self.port = None

        endpoint = capabilities.url
        if capabilities.camera_index is None and not endpoint:
            endpoint = self._endpoint_from_env()

        # Initialize webcam, HTTP endpoint or TCP connection
        if self.camera_index is not None:
            self.cap = cv2.VideoCapture(self.camera_index)
        elif endpoint and urlparse(endpoint).scheme.lower() in HTTP_SCHEMES:
            self.http_url = endpoint
            self._http_token = self._resolve_token(capabilities)
        elif endpoint:
            self.ip_address, self.port = self._parse_ip_port(endpoint)
            self.sock = self.create_tcp_connection(ip=self.ip_address, port=self.port)
        else:
            raise ValueError("No camera_index or valid URL provided")

        self.camera_screenshot_config = config

    @staticmethod
    def _parse_ip_port(endpoint: str) -> tuple[str, int]:
        """Split an ``ip:port`` endpoint for the raw-TCP protocol."""
        try:
            ip_port = str(endpoint).split(":")
            if len(ip_port) != 2:
                raise ValueError(
                    f"Invalid URL format: {endpoint}. Expected format: ip:port"
                )
            ip_address, port_str = ip_port
            return ip_address, int(port_str)
        except (ValueError, TypeError) as e:
            internal_logger.error(f"Failed to parse URL {endpoint}: {e}")
            raise ValueError(f"Invalid URL format: {endpoint}")

    @staticmethod
    def _deskew_matrix(capabilities: CapabilitiesConfig) -> np.ndarray | None:
        if not capabilities.deskew_corners:
            return None
        right, bottom = capabilities.out_width - 1, capabilities.out_height - 1
        return cv2.getPerspectiveTransform(
            corner_points(capabilities.deskew_corners),
            np.array([[0, 0], [right, 0], [right, bottom], [0, bottom]], dtype=np.float32),
        )

    def capture(self) -> np.ndarray:
        """The screen from the webcam, the HTTP endpoint or the TCP connection, deskewed and rotated as configured.

        Returns an empty array when a TCP read fails.
        """
        frame = self._capture_frame()
        if frame is None or frame.size == 0:
            return frame
        if self._deskew is not None:
            frame = cv2.warpPerspective(frame, self._deskew, (self.out_width, self.out_height))
        if self.rotation:
            frame = cv2.rotate(frame, ROTATIONS[self.rotation])
        return frame

    def _capture_frame(self) -> np.ndarray:
        if self.http_url:
            return self.take_http_screenshot()
        if self.sock:
            try:
                frame = self.take_screenshot()
                if frame is None:  # take_screenshot might return None on error
                    internal_logger.warning(
                        "Failed to capture frame from TCP connection."
                    )
                return frame
            except (ConnectionError, socket.timeout, socket.error, ValueError) as e:
                internal_logger.error(
                    f"TCP connection error during capture: {e}. Attempting to re-establish."
                )
                self.sock = self.create_tcp_connection(
                    ip=self.ip_address, port=self.port
                )
                if self.sock:
                    internal_logger.info(
                        "TCP connection re-established. Retrying capture."
                    )
                    return self.take_screenshot()  # Retry capture
                else:
                    internal_logger.error("Failed to re-establish TCP connection.")
                    raise RuntimeError("Failed to re-establish TCP connection.") from e
        if self.cap is None or not self.cap.isOpened():
            internal_logger.error("No valid webcam connection.")
            raise RuntimeError("No valid webcam connection.")

        ret, frame = self.cap.read()
        if ret:
            return frame
        internal_logger.error("Failed to capture frame from webcam.")
        raise RuntimeError("Failed to capture frame from webcam.")

    def __del__(self):
        """Release the camera or socket when the object is destroyed."""
        if hasattr(self, "cap") and self.cap is not None and self.cap.isOpened():
            self.cap.release()
        if hasattr(self, "sock") and self.sock is not None:
            self.sock.close()

    def locate(self, element, index, *args, **kwargs) -> tuple:
        internal_logger.exception(
            "CameraScreenshot does not support locating elements."
        )
        raise NotImplementedError(
            "CameraScreenshot does not support locating elements."
        )

    def assert_elements(self, elements, timeout=None, rule=None):
        internal_logger.exception(
            "CameraScreenshot does not support asserting elements."
        )
        raise NotImplementedError(
            "CameraScreenshot does not support asserting elements."
        )

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None):
        internal_logger.exception(
            "CameraScreenshot does not support getting interactive elements."
        )
        raise NotImplementedError(
            "CameraScreenshot does not support getting interactive elements."
        )

    def create_tcp_connection(self, ip, port):
        tcp_timeout_seconds = 5
        port = int(port)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(tcp_timeout_seconds)
            sock.connect((ip, port))
            internal_logger.debug(f"TCP connection established with {ip}:{port}")
            return sock
        except socket.timeout:
            internal_logger.error(f"TCP connection to {ip}:{port} timed out.")
            return None
        except ConnectionRefusedError:
            internal_logger.error(
                f"TCP connection to {ip}:{port} refused. Is the server running?"
            )
            return None
        except socket.error as e:
            internal_logger.error(f"Error creating TCP connection to {ip}:{port}: {e}")
            return None
        except Exception as e:
            internal_logger.error(
                f"An unexpected error occurred during TCP connection setup: {e}"
            )
            return None

    @staticmethod
    def _endpoint_from_env() -> str | None:
        """Return the controller-injected endpoint, or None when none is set.

        The HTTP URL wins over the legacy raw-TCP port.
        """
        env_url = os.environ.get(ENV_SCREENSHOT_URL)
        if env_url:
            return env_url
        tcp_port = os.environ.get(ENV_SCREENSHOT_TCP_PORT)
        if tcp_port:
            return f"{ENV_TCP_FALLBACK_HOST}:{tcp_port}"
        return None

    @staticmethod
    def _resolve_token(capabilities: "CapabilitiesConfig") -> str | None:
        """Config token first, then ``WEBCAM_SCREENSHOT_TOKEN``."""
        if capabilities.token is not None:
            return capabilities.token.get_secret_value()
        return os.environ.get(ENV_SCREENSHOT_TOKEN) or None

    @staticmethod
    def _http_error(response: requests.Response) -> CameraScreenshotHttpError:
        code: str | None = None
        message: str | None = None
        try:
            body = response.json()
            if isinstance(body, dict):
                code = str(body.get("code")) if body.get("code") is not None else None
                message = (
                    str(body.get("message")) if body.get("message") is not None else None
                )
        except ValueError:
            message = response.text[:HTTP_ERROR_MESSAGE_LIMIT] or None
        if message:
            message = message[:HTTP_ERROR_MESSAGE_LIMIT]
        return CameraScreenshotHttpError(response.status_code, code, message)

    def take_http_screenshot(self) -> np.ndarray:
        """
        Fetch a JPEG from the HTTP screenshot endpoint and decode it.

        Raises:
            CameraScreenshotHttpError: the server answered with a non-200 status.
            RuntimeError: the request failed.
            ValueError: the body is not a decodable image.
        """
        headers = {}
        if self._http_token:
            headers["Authorization"] = f"Bearer {self._http_token}"
        try:
            # Redirects are never followed: they would re-send the bearer token.
            response = requests.get(
                str(self.http_url),
                headers=headers,
                timeout=HTTP_TIMEOUT_SECONDS,
                allow_redirects=False,
            )
        except requests.RequestException as e:
            internal_logger.error(f"HTTP screenshot request failed: {type(e).__name__}")
            raise RuntimeError(
                f"Camera screenshot request to {self.http_url} failed: {type(e).__name__}"
            ) from e
        if response.status_code != HTTP_OK:
            error = self._http_error(response)
            internal_logger.error(str(error))
            raise error
        screenshot = cv2.imdecode(
            np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR
        )
        if screenshot is None:
            internal_logger.error("Failed to decode the HTTP screenshot body.")
            raise ValueError(
                "Failed to decode image data. Data might be corrupt or invalid."
            )
        return screenshot

    def take_screenshot(self):
        """
        Takes a screenshot using the provided socket and returns the image data as an OpenCV image.
        """
        try:
            if not self.sock:
                raise ConnectionError(
                    "No valid socket connection. Unable to take screenshot."
                )

            # Send the screenshot command
            self.sock.sendall(b"screenshot\n")
            internal_logger.debug("Taking screenshot...")

            # Read the image size header (configurable size and byte order)
            size_data = b""
            while len(size_data) < self.header_size:
                chunk = self.sock.recv(self.header_size - len(size_data))
                if not chunk:
                    internal_logger.error(
                        "Connection closed while reading image size header."
                    )
                    raise ConnectionError("Connection closed while reading header.")
                size_data += chunk

            image_length = int.from_bytes(
                size_data, byteorder=self.byte_order, signed=False
            )
            internal_logger.debug(f"Expected image data length: {image_length} bytes")

            # Initialize a bytearray for the image data
            image_data = bytearray()
            remaining_bytes = image_length

            while remaining_bytes > 0:
                chunk_size = min(
                    4096, remaining_bytes
                )  # Increased chunk size for potentially better performance
                try:
                    chunk = self.sock.recv(chunk_size)
                except socket.timeout as exc:
                    internal_logger.error(
                        f"Socket receive timed out while reading image data. Remaining: {remaining_bytes} bytes."
                    )
                    raise TimeoutError(
                        "Socket receive timed out during image data transfer."
                    ) from exc
                except socket.error as e:
                    internal_logger.error(
                        f"Socket error during image data transfer: {e}"
                    )
                    raise ConnectionError(
                        f"Socket error during image data transfer: {e}"
                    ) from e

                if not chunk:
                    internal_logger.error(
                        "Socket connection closed prematurely while receiving image data."
                    )
                    raise ConnectionError("Socket connection closed prematurely.")
                image_data.extend(chunk)
                remaining_bytes -= len(chunk)
            internal_logger.debug("Transferred image data...")

            # Convert the received image data to a numpy array
            nparr = np.frombuffer(image_data, np.uint8)

            # Decode the image data to an OpenCV image
            screenshot = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if screenshot is None:
                internal_logger.error(
                    "cv2.imdecode failed to decode image data. Data might be corrupt or invalid."
                )
                raise ValueError(
                    "Failed to decode image data. Data might be corrupt or invalid."
                )
            internal_logger.debug("Image ready, setting screenshot")
            return screenshot
        except (ConnectionError, ValueError, TimeoutError, socket.error) as e:
            internal_logger.error(f"An error occurred during take_screenshot: {e}")
            # Do not re-raise if you want the capture method to handle reconnection.
            # Return None to indicate failure.
            return np.array([])
        except Exception as e:
            internal_logger.error(
                f"An unexpected error occurred in take_screenshot: {e}"
            )
            raise RuntimeError(f"An unexpected error occurred in take_screenshot: {e}")

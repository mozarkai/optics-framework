from itertools import combinations
from typing import Callable, Optional, Dict, Any, List, Literal
import json
import os
import socket
import time
from urllib.parse import urlparse
import cv2
import numpy as np
import requests
import urllib3
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator, model_validator
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.image_decode import MAX_ENCODED_BYTES, check_encoded_length, decode_image
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.session_liveness import raise_if_session_dead
from optics_framework.engines.elementsources import screen_finder


ENV_SCREENSHOT_URL = "WEBCAM_SCREENSHOT_URL"
ENV_SCREENSHOT_TOKEN = "WEBCAM_SCREENSHOT_TOKEN"  # nosec B105 - an env var name, not a secret
ENV_SCREENSHOT_TCP_PORT = "WEBCAM_SCREENSHOT_TCP_PORT"
ENV_TCP_FALLBACK_HOST = "127.0.0.1"
HTTP_SCHEMES = frozenset({"http", "https"})
HTTP_TIMEOUT_SECONDS = 15
HTTP_OK = 200
ROTATIONS = {"clockwise": cv2.ROTATE_90_CLOCKWISE, "counterclockwise": cv2.ROTATE_90_COUNTERCLOCKWISE}
HTTP_ERROR_FIELD_LIMIT = 300
HTTP_ERROR_BODY_LIMIT = 4096
# Larger than any screen; a remote caller sets the size, and the frame is allocated at it.
MAX_OUTPUT_SIDE = 8192
CAPTURE_DEADLINE_SECONDS = 15
READ_CHUNK_BYTES = 65536
# Backends clamp a size they cannot give to the largest one they can.
LARGEST_SIZE = 10000
COMMON_SIZES = ((3840, 2160), (2560, 1440), (1920, 1080), (1280, 720))
SETTLE_SECONDS = 1.0
MAX_BUFFERED_FRAMES = 10
DEFAULT_FPS = 30.0
EXPOSURE_MODES = {"V4L2": (1, 3), "DSHOW": (0.25, 0.75)}
EXPOSURE_DRIFT = 0.25
BRIGHTNESS_STEPS = 8
BRIGHTNESS_RAMP_SECONDS = 1.0
AVERAGED_FRAMES = 3
SCREEN_OFF_SECONDS = 3.0
SCREEN_OFF_POLL_SECONDS = 0.5


def _bounded(value: object) -> str | None:
    return (str(value)[:HTTP_ERROR_FIELD_LIMIT] or None) if value is not None else None


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError(f"Camera screenshot took longer than {CAPTURE_DEADLINE_SECONDS}s")
    return remaining


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
    out_width: int = Field(1080, gt=0, le=MAX_OUTPUT_SIDE, description="Output width of the screenshot")
    out_height: int = Field(1920, gt=0, le=MAX_OUTPUT_SIDE, description="Output height of the screenshot")
    deskew_corners: Optional[list[str]] = Field(
        None,
        description="The screen's corners in the camera frame as 'x,y': top-left, top-right, bottom-right, bottom-left",
    )
    find_screen: Optional[Literal["brightness", "frame"]] = Field(
        None,
        description=(
            "Find the screen's corners in the camera frame instead of deskew_corners: 'brightness' with the driver's"
            " brightness keys, 'frame' from one frame of a bright screen"
        ),
    )
    camera_width: Optional[int] = Field(None, gt=0, description="Width the webcam captures at; the largest it gives when unset")
    camera_height: Optional[int] = Field(None, gt=0, description="Height the webcam captures at; the largest it gives when unset")
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

    @model_validator(mode="after")
    def _settings_agree(self) -> "CapabilitiesConfig":
        if self.deskew_corners and self.find_screen:
            raise ValueError("set deskew_corners or find_screen, not both")
        if (self.camera_width is None) != (self.camera_height is None):
            raise ValueError("set both camera_width and camera_height, or neither")
        return self

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
        self.find_screen = capabilities.find_screen
        self._deskew: np.ndarray | None = None
        self._corners = np.zeros((4, 2), np.float32)
        self._guard: screen_finder.ViewGuard | None = None
        if capabilities.deskew_corners:
            self._use_corners(corner_points(capabilities.deskew_corners))
        # A driver reads matches in its own frame, so a frame of another size is scaled to the size asked for.
        self._resize = (
            self._deskew is None and not self.find_screen
            and bool({"out_width", "out_height"} & capabilities.model_fields_set)
        )
        self.header_size: int = capabilities.header_size
        self.byte_order: Literal["little", "big"] = capabilities.byte_order

        self.http_url: str | None = None
        self._http_token: str | None = None
        self.sock = None
        self.cap = None
        self._frame_period = 1 / DEFAULT_FPS
        self.ip_address = None
        self.port = None

        endpoint, token = self._endpoint_and_token(capabilities)

        if self.camera_index is not None:
            self.cap = self._open_webcam(self.camera_index, capabilities.camera_width, capabilities.camera_height)
        elif endpoint and urlparse(endpoint).scheme.lower() in HTTP_SCHEMES:
            self.http_url = endpoint
            self._http_token = token
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

    def _use_corners(self, corners: np.ndarray) -> None:
        right, bottom = self.out_width - 1, self.out_height - 1
        self._deskew = cv2.getPerspectiveTransform(
            corners.astype(np.float32),
            np.array([[0, 0], [right, 0], [right, bottom], [0, bottom]], dtype=np.float32),
        )
        self._corners = corners
        self._guard = None

    def capture(self) -> np.ndarray:
        """The screen from the webcam, the HTTP endpoint or the TCP connection, deskewed and rotated as configured."""
        frame = self._capture_frame()
        if frame is None or frame.size == 0:
            return frame
        if self.find_screen and self._deskew is None:
            self._calibrate(frame)
            frame = self._capture_frame()
        if self._deskew is not None:
            frame = self._screen(frame)
        elif self._resize and frame.shape[:2] != (self.out_height, self.out_width):
            frame = cv2.resize(frame, (self.out_width, self.out_height), interpolation=cv2.INTER_AREA)
        if self.rotation:
            frame = cv2.rotate(frame, ROTATIONS[self.rotation])
        return frame

    def _screen(self, frame: np.ndarray) -> np.ndarray:
        """The deskewed screen; E0305 when the phone left its place or the screen stays dark."""
        if self._guard is None:
            self._guard = screen_finder.ViewGuard(frame, self._corners)
            if not self._guard.anchored:
                internal_logger.info("The area around the screen has no texture, so a moved phone or camera goes unnoticed")
        elif self._guard.moved(frame):
            if not self.find_screen:
                raise self._blind("the phone or the camera moved since the first frame; set deskew_corners again", frame)
            internal_logger.warning("The phone or the camera moved; finding the screen again")
            self._calibrate(frame)
            frame = self._capture_frame()
        deadline = time.monotonic() + SCREEN_OFF_SECONDS
        screen = self._warp(frame)
        while screen_finder.screen_dark(screen):
            if time.monotonic() >= deadline:
                raise self._blind("the screen is off", frame)
            time.sleep(SCREEN_OFF_POLL_SECONDS)
            frame = self._capture_frame()
            screen = self._warp(frame)
        return screen

    def _warp(self, frame: np.ndarray) -> np.ndarray:
        return cv2.warpPerspective(frame, self._deskew, (self.out_width, self.out_height))

    def _calibrate(self, frame: np.ndarray) -> None:
        aspect = self.out_width / self.out_height
        reasons = []
        for name, signals in self._screen_signals(frame):
            try:
                corners = screen_finder.find_screen(*signals(), aspect)
                break
            except screen_finder.ScreenNotFound as exc:
                internal_logger.info(f"No screen found by {name}: {exc}")
                reasons.append(str(exc))
        else:
            raise self._blind(f"no screen found: {'; '.join(reasons)}", frame)
        self._use_corners(corners)
        found = ", ".join(f'"{x:.0f},{y:.0f}"' for x, y in corners)
        internal_logger.info(f"Found the screen in the camera frame by {name}: deskew_corners: [{found}]")
        marked = frame.copy()
        cv2.polylines(marked, [corners.astype(np.int32)], True, (0, 255, 0), 2)
        self._save(marked, "camera_screen_found")

    def _screen_signals(self, frame: np.ndarray) -> list[tuple[str, Callable[[], tuple[np.ndarray, np.ndarray]]]]:
        """The ways to find the screen, best first; brightness falls back to one frame when the keys do nothing."""
        ways = [("one frame", lambda: screen_finder.frame_signals(frame))]
        if self.find_screen != "brightness":
            return ways
        if self.driver is None:
            raise OpticsError(Code.E0501, message="find_screen: brightness presses the brightness keys of a driver; enable one, or use find_screen: frame")
        try:
            dim = self._at_brightness("brightnessdown")
            bright = self._at_brightness("brightnessup")
        except Exception as exc:
            raise_if_session_dead(exc)
            internal_logger.warning(f"The driver did not press the brightness keys ({exc}); finding the screen in one frame")
            return ways
        return [("brightness", lambda: screen_finder.brightness_signals(dim, bright)), *ways]

    def _at_brightness(self, key: str) -> np.ndarray:
        for _ in range(BRIGHTNESS_STEPS):
            self.driver.press_keycode(key)
        time.sleep(BRIGHTNESS_RAMP_SECONDS)
        return np.mean([self._capture_frame().astype(np.float32) for _ in range(AVERAGED_FRAMES)], axis=0)

    def _blind(self, cause: str, frame: np.ndarray) -> OpticsError:
        saved = self._save(frame, "camera_blind")
        where = f" The camera frame is saved as {saved}." if saved else ""
        internal_logger.error(f"The camera does not see the device screen: {cause}.{where}")
        return OpticsError(Code.E0305, message=f"The camera does not see the device screen: {cause}.{where}")

    def _save(self, frame: np.ndarray, name: str) -> str | None:
        try:
            folder = self.driver.event_sdk.config_handler.config.capture_dir
        except AttributeError:
            return None
        if not folder:
            return None
        path = os.path.join(folder, f"{name}_{time.strftime('%Y%m%d-%H%M%S')}.jpg")
        os.makedirs(folder, exist_ok=True)
        return path if cv2.imwrite(path, frame) else None

    def _open_webcam(self, index: int, width: int | None, height: int | None) -> cv2.VideoCapture:
        cap = cv2.VideoCapture(index)
        if not cap.isOpened():
            internal_logger.error(f"Cannot open the camera at index {index}")
            return cap
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._request_size(cap, width, height)
        fps = cap.get(cv2.CAP_PROP_FPS)
        self._frame_period = 1 / (fps if 1 <= fps <= 240 else DEFAULT_FPS)
        settle_until = time.monotonic() + SETTLE_SECONDS
        while time.monotonic() < settle_until:
            if not cap.grab():
                break
        locked = self._lock_controls(cap)
        ok, frame = cap.read()
        if not ok:
            internal_logger.error(f"The camera at index {index} gives no frames")
            return cap
        internal_logger.info(
            f"Camera {index} ({cap.getBackendName()}) captures {frame.shape[1]}x{frame.shape[0]} at"
            f" {1 / self._frame_period:.0f} fps; locked: {', '.join(locked) or 'nothing'}"
        )
        return cap

    @staticmethod
    def _request_size(cap: cv2.VideoCapture, width: int | None, height: int | None) -> None:
        if width and height:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            granted = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            if granted != (width, height):
                internal_logger.warning(f"The camera gives {granted[0]}x{granted[1]}, not {width}x{height}")
            return
        default = cap.get(cv2.CAP_PROP_FRAME_WIDTH) * cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, LARGEST_SIZE)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, LARGEST_SIZE)
        if cap.get(cv2.CAP_PROP_FRAME_WIDTH) * cap.get(cv2.CAP_PROP_FRAME_HEIGHT) > default:
            return
        for size in COMMON_SIZES:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])
            if (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))) == size:
                return

    @staticmethod
    def _lock_controls(cap: cv2.VideoCapture) -> list[str]:
        """Fix focus, white balance and exposure where the backend lets it, so frames do not drift between reads."""
        locked = [name for name, prop in (("focus", cv2.CAP_PROP_AUTOFOCUS), ("white balance", cv2.CAP_PROP_AUTO_WB)) if cap.set(prop, 0)]
        modes = EXPOSURE_MODES.get(cap.getBackendName())
        ok, before = cap.read()
        if modes is None or not ok:
            return locked
        exposure = cap.get(cv2.CAP_PROP_EXPOSURE)
        if cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, modes[0]) and cap.set(cv2.CAP_PROP_EXPOSURE, exposure):
            ok, after = cap.read()
            if ok and abs(float(after.mean()) - float(before.mean())) <= EXPOSURE_DRIFT * max(float(before.mean()), 1.0):
                return [*locked, "exposure"]
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, modes[1])
        return locked

    def _capture_frame(self) -> np.ndarray:
        if self.http_url:
            return self.take_http_screenshot()
        if self.ip_address is not None:
            try:
                return self._take_tcp_frame()
            except ConnectionError as e:
                internal_logger.warning(f"TCP camera connection lost: {e}. Reconnecting once.")
                return self._take_tcp_frame()
        if self.cap is None or not self.cap.isOpened():
            internal_logger.error("No valid webcam connection.")
            raise RuntimeError("No valid webcam connection.")
        return self._fresh_frame()

    def _fresh_frame(self) -> np.ndarray:
        """A frame exposed after this call: buffered frames come back at once, so grab until one has to wait."""
        for _ in range(MAX_BUFFERED_FRAMES):
            started = time.monotonic()
            if not self.cap.grab():
                break
            if time.monotonic() - started >= self._frame_period / 3:
                break
        # The frame that waited may have started its exposure before this call.
        if self.cap.grab():
            ok, frame = self.cap.retrieve()
            if ok:
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
    def _endpoint_and_token(capabilities: CapabilitiesConfig) -> tuple[str | None, str | None]:
        """The endpoint and its bearer token; the server's env token goes only to the server's env endpoint."""
        token = capabilities.token.get_secret_value() if capabilities.token else None
        if capabilities.camera_index is None and not capabilities.url:
            return CameraScreenshot._endpoint_from_env(), token or os.environ.get(ENV_SCREENSHOT_TOKEN) or None
        if capabilities.url and not token and os.environ.get(ENV_SCREENSHOT_TOKEN):
            internal_logger.warning(
                f"{ENV_SCREENSHOT_TOKEN} is sent only to {ENV_SCREENSHOT_URL}; for a url in"
                f" capabilities, set capabilities.token (e.g. token: ${{{ENV_SCREENSHOT_TOKEN}}})"
            )
        return capabilities.url, token

    @staticmethod
    def _http_error(status: int, body: bytes) -> CameraScreenshotHttpError:
        try:
            fields = json.loads(body)
        except (ValueError, RecursionError):
            fields = {"message": body.decode("utf-8", "replace")}
        if not isinstance(fields, dict):
            fields = {}
        return CameraScreenshotHttpError(status, _bounded(fields.get("code")), _bounded(fields.get("message")))

    @staticmethod
    def _read_capped(response: requests.Response, limit: int, deadline: float) -> bytes:
        body = bytearray()
        # read1 returns whatever one socket read gives, so a slow drip cannot stretch a read past the deadline.
        while len(body) < limit:
            _remaining(deadline)
            chunk = response.raw.read1(READ_CHUNK_BYTES, decode_content=True)
            if not chunk:
                break
            body += chunk
        return bytes(body[:limit])

    def take_http_screenshot(self) -> np.ndarray:
        """
        Fetch an image from the HTTP screenshot endpoint and decode it.

        Raises:
            CameraScreenshotHttpError: the server answered with a non-200 status.
            RuntimeError: the request failed.
            TimeoutError: the transfer outlasted the capture deadline.
            OpticsError: E0304, the body is too large or not a decodable image.
        """
        headers = {}
        if self._http_token:
            headers["Authorization"] = f"Bearer {self._http_token}"
        deadline = time.monotonic() + CAPTURE_DEADLINE_SECONDS
        try:
            # Redirects are never followed: they would re-send the bearer token.
            with requests.get(
                str(self.http_url),
                headers=headers,
                timeout=HTTP_TIMEOUT_SECONDS,
                allow_redirects=False,
                stream=True,
            ) as response:
                if response.status_code != HTTP_OK:
                    error = self._http_error(
                        response.status_code, self._read_capped(response, HTTP_ERROR_BODY_LIMIT, deadline)
                    )
                    internal_logger.error(str(error))
                    raise error
                body = self._read_capped(response, MAX_ENCODED_BYTES + 1, deadline)
        except (requests.RequestException, urllib3.exceptions.HTTPError) as e:
            internal_logger.error(f"HTTP screenshot request failed: {type(e).__name__}")
            raise RuntimeError(
                f"Camera screenshot request to {self.http_url} failed: {type(e).__name__}"
            ) from e
        return decode_image(body)

    def _take_tcp_frame(self) -> np.ndarray:
        if self.sock is None:
            self.sock = self.create_tcp_connection(ip=self.ip_address, port=self.port)
            if self.sock is None:
                raise ConnectionError(f"Cannot connect to the TCP camera at {self.ip_address}:{self.port}")
        try:
            return self.take_screenshot()
        except Exception:
            # A failed frame leaves unread bytes on the stream, so the next capture needs a new connection.
            self.sock.close()
            self.sock = None
            raise

    def _recv_exact(self, size: int, deadline: float) -> bytes:
        data = bytearray()
        while len(data) < size:
            self.sock.settimeout(_remaining(deadline))
            chunk = self.sock.recv(min(READ_CHUNK_BYTES, size - len(data)))
            if not chunk:
                raise ConnectionError("The TCP camera closed the connection mid-frame.")
            data += chunk
        return bytes(data)

    def take_screenshot(self) -> np.ndarray:
        """Request one frame over the raw-TCP protocol (length header, then the image) and decode it."""
        deadline = time.monotonic() + CAPTURE_DEADLINE_SECONDS
        self.sock.settimeout(_remaining(deadline))
        self.sock.sendall(b"screenshot\n")
        image_length = int.from_bytes(self._recv_exact(self.header_size, deadline), byteorder=self.byte_order)
        check_encoded_length(image_length)
        return decode_image(self._recv_exact(image_length, deadline))

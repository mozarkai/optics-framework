"""CameraScreenshot finds the phone screen in a camera frame and names why it cannot see it."""
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.screen_snapshot import read_screen
from optics_framework.common.strategies import StrategyManager
from optics_framework.engines.elementsources import camera_screenshot, webcam as webcam_module
from optics_framework.engines.elementsources.camera_screenshot import CameraScreenshot

SCREEN_W, SCREEN_H = 180, 390
CAMERA = (960, 540)
LENS_TRAVEL_SECONDS = 0.02


def _content() -> np.ndarray:
    screen = np.full((SCREEN_H, SCREEN_W, 3), 215, np.uint8)
    for row in range(60, SCREEN_H - 20, 40):
        cv2.rectangle(screen, (20, row), (20 + (row * 7) % 120, row + 12), (40, 40, 40), -1)
    screen[8:38, 8:38] = (0, 0, 230)
    return screen


CONTENT = _content()
DARK = np.where(CONTENT == 215, 0, 255 - CONTENT).astype(np.uint8)
BACKGROUND = cv2.GaussianBlur(
    np.random.default_rng(1).integers(0, 160, (CAMERA[1], CAMERA[0], 3)).astype(np.uint8), (0, 0), 3
)


def _placement(angle: float, centre: tuple[float, float]) -> np.ndarray:
    turn = cv2.getRotationMatrix2D((SCREEN_W / 2, SCREEN_H / 2), angle, 1.15)
    turn[:, 2] += np.array(centre) - np.array([SCREEN_W / 2, SCREEN_H / 2])
    return np.vstack([turn, [0, 0, 1]])


def _scene(level: float, placement: np.ndarray | None, screen_on: bool = True, content=CONTENT, pointer=None) -> np.ndarray:
    frame = BACKGROUND.astype(np.float32)
    if placement is not None:
        bezel = 14
        body = np.full((SCREEN_H + 2 * bezel, SCREEN_W + 2 * bezel), 255, np.uint8)
        shift = np.array([[1, 0, -bezel], [0, 1, -bezel], [0, 0, 1]], np.float64)
        cover = cv2.warpPerspective(body, placement @ shift, CAMERA) > 0
        frame[cover] = 25
        shown = content.copy()
        if pointer is not None:
            cv2.circle(shown, (round(pointer[0]), round(pointer[1])), 4, (150, 150, 150), -1)
        lit = shown.astype(np.float32) * (level if screen_on else 0)
        screen = cv2.warpPerspective(lit, placement, CAMERA)
        inside = cv2.warpPerspective(np.full((SCREEN_H, SCREEN_W), 255, np.uint8), placement, CAMERA) > 0
        frame[inside] = screen[inside]
    return cv2.GaussianBlur(np.clip(frame, 0, 255).astype(np.uint8), (0, 0), 0.8)


class _Phone:
    """A device whose brightness keys change what the fake camera shows."""

    def __init__(self, capture_dir: str | None):
        self.level = 1.0
        self.keys: list[str] = []
        self.placement: np.ndarray | None = _placement(6, (480, 270))
        self.screen_on = True
        self.content, self.pointer = CONTENT, None
        self.event_sdk = SimpleNamespace(config_handler=SimpleNamespace(config=SimpleNamespace(capture_dir=capture_dir)))

    def press_keycode(self, keycode: str, event_name=None) -> None:
        self.keys.append(keycode)
        self.level = min(1.0, max(0.3, self.level + (0.1 if keycode == "brightnessup" else -0.1)))

    def supports(self, capability: str) -> bool:
        return True

    def point_at(self, point) -> None:
        self.pointer = point and (point[0] * SCREEN_W, point[1] * SCREEN_H)

    def frame(self) -> np.ndarray:
        return _scene(self.level, self.placement, self.screen_on, self.content, self.pointer)


@pytest.fixture
def phone(request, tmp_path):
    return getattr(request, "param", _Phone)(str(tmp_path))


@pytest.fixture
def camera(phone, monkeypatch):
    monkeypatch.setattr(camera_screenshot, "BRIGHTNESS_RAMP_SECONDS", 0)
    monkeypatch.delenv("WEBCAM_SCREENSHOT_URL", raising=False)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = cv2.imencode(".bmp", phone.frame())[1].tobytes()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/frame"
    server.shutdown()
    server.server_close()


def _source(url: str, driver, find_screen: str) -> CameraScreenshot:
    capabilities = {"url": url, "find_screen": find_screen, "out_width": SCREEN_W, "out_height": SCREEN_H}
    return CameraScreenshot({"capabilities": capabilities}, driver=driver)


def _shows_the_screen(frame: np.ndarray, content: np.ndarray = CONTENT) -> bool:
    if frame.shape != content.shape:
        return False
    wrong = np.abs(frame.astype(np.int16) - content.astype(np.int16)).max(axis=2) > 60
    return float(wrong.mean()) < 0.1


class _NoBrightnessKeys(_Phone):
    def press_keycode(self, keycode: str, event_name=None) -> None:
        self.keys.append(keycode)


class _KeysRefused(_Phone):
    def press_keycode(self, keycode: str, event_name=None) -> None:
        raise ValueError(f"no key {keycode}")


@pytest.mark.parametrize("phone", [_NoBrightnessKeys, _KeysRefused], indirect=True)
def test_brightness_keys_that_do_nothing_fall_back_to_one_frame(camera, phone):
    assert _shows_the_screen(_source(camera, phone, "brightness").capture())


def test_a_screen_that_stays_off_names_the_cause(camera, phone, monkeypatch):
    monkeypatch.setattr(camera_screenshot, "SCREEN_OFF_SECONDS", 0.3)
    source = _source(camera, phone, "frame")
    source.capture()
    phone.screen_on = False
    with pytest.raises(OpticsError) as exc:
        source.capture()
    assert exc.value.code == Code.E0305
    assert "the screen is off" in exc.value.message


class _ReadsNothing:
    def find_element(self, frame, text, index=None):
        return None

    def detect_text(self, frame):
        return []


def test_a_screen_read_names_why_the_camera_cannot_see_the_screen(camera, phone, monkeypatch):
    monkeypatch.setattr(camera_screenshot, "SCREEN_OFF_SECONDS", 0.3)
    source = _source(camera, phone, "frame")
    source.capture()
    phone.screen_on = False
    observer = SimpleNamespace(strategy_manager=StrategyManager(InstanceFallback([source]), _ReadsNothing(), None), element_source=source)
    with pytest.raises(OpticsError) as exc:
        read_screen(observer)
    assert exc.value.code == Code.E0305
    assert "the screen is off" in exc.value.message


class _NativeTree(ElementSourceInterface):
    def __init__(self):
        self.looks = 0

    def capture(self): raise NotImplementedError
    def locate(self, element, index=None): return None
    def assert_elements(self, elements, timeout=30, rule="any"): raise NotImplementedError
    def get_interactive_elements(self, filter_config=None): return []

    def check_elements(self, elements, visible=False):
        self.looks += 1
        return [self.looks >= 2 for _ in elements]


def test_an_assertion_keeps_reading_the_native_tree_while_the_camera_is_blind(camera, phone):
    source = _source(camera, phone, "frame")
    source.capture()
    phone.placement = None
    tree = _NativeTree()
    strategies = StrategyManager(InstanceFallback([tree, source]), _ReadsNothing(), None)
    assert strategies.assert_presence(["Settings"], "Text", timeout=5)
    assert tree.looks == 2


@pytest.mark.parametrize(
    ("find_screen", "rotation"),
    [("pointer", None), ("pointer", "clockwise"), ("pointer", "counterclockwise"), ("auto", "counterclockwise")],
)
def test_the_pointer_finds_a_screen_black_to_its_edges(camera, phone, find_screen, rotation, monkeypatch):
    monkeypatch.setattr(camera_screenshot, "POINTER_SETTLE_SECONDS", 0)
    phone.content = DARK
    size = (SCREEN_H, SCREEN_W) if rotation else (SCREEN_W, SCREEN_H)
    capabilities = {"url": camera, "find_screen": find_screen, "out_width": size[0], "out_height": size[1], "rotation": rotation}
    source = CameraScreenshot({"capabilities": capabilities}, driver=phone)
    assert _shows_the_screen(source.capture(), DARK)
    assert phone.pointer is None


def _blurred(frame: np.ndarray, sigma: float) -> np.ndarray:
    """A Gaussian blur; a wide one runs on a smaller copy, since at full size it took seconds per lens sweep."""
    if sigma <= 4:
        return cv2.GaussianBlur(frame, (0, 0), sigma)
    scale = sigma / 2
    height, width = frame.shape[:2]
    small = cv2.resize(frame, (round(width / scale), round(height / scale)), interpolation=cv2.INTER_AREA)
    return cv2.resize(cv2.GaussianBlur(small, (0, 0), 2), (width, height), interpolation=cv2.INTER_LINEAR)


class _Webcam:
    """A webcam sharp only within one lens step of ``peak`` and once the lens stops; ``None`` is its own autofocus, hunting."""

    span = range(0, 256, 5)
    key = "046d:085e"

    def __init__(self, view):
        self.view, self.value, self.peak, self.moves, self.moved_at = view, None, 35, 0, 0.0

    def focus(self, value):
        self.value, self.moves, self.moved_at = value, self.moves + 1, time.monotonic()

    def retrieve(self):
        moving = time.monotonic() - self.moved_at < LENS_TRAVEL_SECONDS
        blur = 30.0 if self.value is None or moving else 0.3 + 0.4 * abs(self.value - self.peak)
        return True, _blurred(self.view(), blur)

    read = retrieve

    def get(self, prop): return {cv2.CAP_PROP_FRAME_WIDTH: CAMERA[0], cv2.CAP_PROP_FRAME_HEIGHT: CAMERA[1]}.get(prop, 30.0)
    def set(self, prop, value): return False
    def grab(self): return True
    def release(self): pass
    def getBackendName(self): return "AVFOUNDATION"
    isOpened = grab


@pytest.fixture
def webcam(monkeypatch, tmp_path):
    monkeypatch.setenv("OPTICS_HOME", str(tmp_path))
    monkeypatch.setattr(webcam_module, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(webcam_module, "camera_access", lambda: "authorized")
    monkeypatch.setattr(camera_screenshot, "LENS_SETTLE_SECONDS", 2.5 * LENS_TRAVEL_SECONDS)
    monkeypatch.setenv("WEBCAM_SCREENSHOT_URL", "http://127.0.0.1:1/screenshot")

    def use(view, lens=None, **screen):
        camera = _Webcam(view)
        monkeypatch.setattr(camera_screenshot.cv2, "VideoCapture", lambda index: camera)
        monkeypatch.setattr(camera_screenshot.camera_lens, "uvc_lens", lambda: lens or camera)
        capabilities = {"camera_index": 0, "out_width": SCREEN_W, "out_height": SCREEN_H, **screen}
        return camera, CameraScreenshot({"capabilities": capabilities})

    return use


def test_a_blurred_webcam_focuses_on_the_screen_and_again_on_a_moved_phone(webcam, phone):
    camera, source = webcam(phone.frame, find_screen="frame")
    assert _shows_the_screen(source.capture())
    assert camera.value == 35
    phone.placement, camera.peak = _placement(-4, (430, 290)), 60
    assert _shows_the_screen(source.capture())
    assert camera.value == 60


def test_fixed_corners_hold_the_lens_on_a_one_step_peak_that_a_new_session_confirms(webcam, phone):
    screen = np.float32([[0, 0], [SCREEN_W, 0], [SCREEN_W, SCREEN_H], [0, SCREEN_H]])
    corners = [f"{x},{y}" for x, y in cv2.perspectiveTransform(screen[None], phone.placement)[0]]
    camera, source = webcam(phone.frame, deskew_corners=corners)
    assert _shows_the_screen(source.capture())
    assert camera.value == 35
    assert camera.moves > 20
    camera, source = webcam(phone.frame, deskew_corners=corners)
    assert _shows_the_screen(source.capture())
    assert camera.value == 35
    assert camera.moves < 10
    camera, source = webcam(phone.frame, deskew_corners=corners)
    camera.peak = 120
    assert _shows_the_screen(source.capture())
    assert camera.value == 120


def test_another_cameras_lens_is_given_back_its_autofocus_and_left_alone(webcam, phone):
    other = _Webcam(phone.frame)
    camera, source = webcam(phone.frame, lens=other, find_screen="frame")
    camera.value = camera.peak
    assert _shows_the_screen(source.capture())
    assert other.value is None
    moves = other.moves
    phone.placement = _placement(-4, (430, 290))
    assert _shows_the_screen(source.capture())
    assert other.moves == moves

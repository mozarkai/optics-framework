"""CameraScreenshot finds the phone screen in a camera frame and names why it cannot see it."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.strategies import StrategyManager
from optics_framework.engines.elementsources import camera_screenshot
from optics_framework.engines.elementsources.camera_screenshot import CameraScreenshot

SCREEN_W, SCREEN_H = 180, 390
CAMERA = (960, 540)


def _content() -> np.ndarray:
    screen = np.full((SCREEN_H, SCREEN_W, 3), 215, np.uint8)
    for row in range(60, SCREEN_H - 20, 40):
        cv2.rectangle(screen, (20, row), (20 + (row * 7) % 120, row + 12), (40, 40, 40), -1)
    screen[8:38, 8:38] = (0, 0, 230)
    return screen


CONTENT = _content()
BACKGROUND = cv2.GaussianBlur(
    np.random.default_rng(1).integers(0, 160, (CAMERA[1], CAMERA[0], 3)).astype(np.uint8), (0, 0), 3
)


def _placement(angle: float, centre: tuple[float, float]) -> np.ndarray:
    turn = cv2.getRotationMatrix2D((SCREEN_W / 2, SCREEN_H / 2), angle, 1.15)
    turn[:, 2] += np.array(centre) - np.array([SCREEN_W / 2, SCREEN_H / 2])
    return np.vstack([turn, [0, 0, 1]])


def _scene(level: float, placement: np.ndarray | None, screen_on: bool = True) -> np.ndarray:
    frame = BACKGROUND.astype(np.float32)
    if placement is not None:
        bezel = 14
        body = np.full((SCREEN_H + 2 * bezel, SCREEN_W + 2 * bezel), 255, np.uint8)
        shift = np.array([[1, 0, -bezel], [0, 1, -bezel], [0, 0, 1]], np.float64)
        cover = cv2.warpPerspective(body, placement @ shift, CAMERA) > 0
        frame[cover] = 25
        lit = CONTENT.astype(np.float32) * (level if screen_on else 0)
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
        self.event_sdk = SimpleNamespace(config_handler=SimpleNamespace(config=SimpleNamespace(capture_dir=capture_dir)))

    def press_keycode(self, keycode: str, event_name=None) -> None:
        self.keys.append(keycode)
        self.level = min(1.0, max(0.3, self.level + (0.1 if keycode == "brightnessup" else -0.1)))

    def frame(self) -> np.ndarray:
        return _scene(self.level, self.placement, self.screen_on)


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
            body = cv2.imencode(".png", phone.frame())[1].tobytes()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/frame"
    server.shutdown()
    server.server_close()


def _source(url: str, driver, find_screen: str) -> CameraScreenshot:
    capabilities = {"url": url, "find_screen": find_screen, "out_width": SCREEN_W, "out_height": SCREEN_H}
    return CameraScreenshot({"capabilities": capabilities}, driver=driver)


def _shows_the_screen(frame: np.ndarray) -> bool:
    if frame.shape != CONTENT.shape:
        return False
    wrong = np.abs(frame.astype(np.int16) - CONTENT.astype(np.int16)).max(axis=2) > 60
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


def test_one_frame_finds_a_bright_screen_without_a_driver(camera):
    assert _shows_the_screen(_source(camera, None, "frame").capture())


def test_a_moved_phone_is_found_again(camera, phone):
    source = _source(camera, phone, "brightness")
    source.capture()
    phone.placement = _placement(-4, (430, 290))
    assert _shows_the_screen(source.capture())


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


class _NativeTree(ElementSourceInterface):
    def __init__(self):
        self.looks = 0

    def capture(self):
        raise NotImplementedError

    def locate(self, element, index=None):
        return None

    def assert_elements(self, elements, timeout=30, rule="any"):
        raise NotImplementedError

    def get_interactive_elements(self, filter_config=None):
        return []

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

"""CameraScreenshot over HTTP + bearer token, raw TCP, and env fallback."""
import json
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import cv2
import numpy as np
import pytest


from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.factories import ElementSourceFactory
from optics_framework.common.strategies import TextDetectionStrategy
from optics_framework.engines.elementsources.camera_screenshot import CameraScreenshot, CameraScreenshotHttpError

TOKEN = "s3cret-token-value"
ENV_VARS = (
    "WEBCAM_SCREENSHOT_URL",
    "WEBCAM_SCREENSHOT_TOKEN",
    "WEBCAM_SCREENSHOT_TCP_PORT",
)


def _jpeg() -> bytes:
    image = np.zeros((20, 30, 3), np.uint8)
    image[:, :] = (10, 200, 30)
    ok, buf = cv2.imencode(".jpg", image)
    assert ok
    return buf.tobytes()


JPEG = _jpeg()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def http_server():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            received.append((self.path, self.headers.get("Authorization")))
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                self._json(401, "UNAUTHORIZED", "bad token")
                return
            if server.mode == "ok":
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(server.body)))
                self.end_headers()
                self.wfile.write(server.body)
            elif server.mode == "redirect":
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/elsewhere")
                self.end_headers()
            elif server.mode == "plain":
                body = b"boom"
                self.send_response(500)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                status, code = server.mode
                self._json(status, code, f"{code} happened")

        def _json(self, status, code, message):
            body = json.dumps({"code": code, "message": message}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.mode = "ok"
    server.body = JPEG
    server.received = received
    server.url = f"http://127.0.0.1:{server.server_port}/screenshot"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def tcp_server():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    commands = []

    def serve():
        try:
            conn, _ = listener.accept()
        except OSError:
            return
        with conn:
            commands.append(conn.recv(64))
            conn.sendall(len(JPEG).to_bytes(8, "little") + JPEG)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield listener.getsockname()[1], commands
    listener.close()


def _source(**capabilities):
    return CameraScreenshot({"capabilities": capabilities})


class TestHttp:
    def test_wrong_token_is_401(self, http_server):
        source = _source(url=http_server.url, token="wrong")
        with pytest.raises(CameraScreenshotHttpError) as exc:
            source.capture()
        assert exc.value.status == 401
        assert exc.value.code == "UNAUTHORIZED"
        assert "wrong" not in str(exc.value)

    def test_redirect_is_not_followed(self, http_server):
        http_server.mode = "redirect"
        with pytest.raises(CameraScreenshotHttpError) as exc:
            _source(url=http_server.url, token=TOKEN).capture()
        assert exc.value.status == 302
        assert len(http_server.received) == 1

    def test_unreachable_server_raises_runtime_error(self, http_server):
        url = http_server.url
        http_server.shutdown()
        http_server.server_close()
        source = _source(url=url, token=TOKEN)
        with pytest.raises(RuntimeError, match="failed"):
            source.capture()


FULL_FRAME = ["0,0", "29,0", "29,19", "0,19"]


class TestFrameProcessing:
    def test_tcp_frame_is_deskewed_then_rotated(self, tcp_server):
        port, _ = tcp_server
        source = _source(
            url=f"127.0.0.1:{port}", deskew_corners=FULL_FRAME, out_width=60, out_height=40, rotation="clockwise"
        )
        assert source.capture().shape == (60, 40, 3)


class _FakeAppium:
    NAME = "appium"

    def get_window_size(self):
        return {"width": 15, "height": 10}


class _FakeOcr:
    def find_element(self, frame, text, index=None, strict=False):
        return True, (20, 10), ((18, 8), (22, 12))


class TestCameraMatchesInDriverWindow:
    def _locate(self, url, driver):
        config = [{"camera_screenshot": {"enabled": True, "capabilities": {"url": url, "token": TOKEN}}}]
        source = ElementSourceFactory.get_driver(config, InstanceFallback([driver])).instances[0]
        return TextDetectionStrategy(source, _FakeOcr(), None).locate("Login").value

    def test_ocr_point_is_scaled_to_the_appium_window(self, http_server):
        assert self._locate(http_server.url, _FakeAppium()) == (10, 5)


class TestEnvFallback:
    def test_env_token_is_never_sent_to_a_url_from_capabilities(self, http_server, monkeypatch):
        monkeypatch.setenv("WEBCAM_SCREENSHOT_TOKEN", TOKEN)
        with pytest.raises(CameraScreenshotHttpError) as exc:
            _source(url=http_server.url).capture()
        assert exc.value.status == 401
        assert http_server.received == [("/screenshot", None)]


class TestTokenNotLogged:
    def test_no_log_line_contains_token(self, http_server, caplog):
        with caplog.at_level(logging.DEBUG):
            _source(url=http_server.url, token=TOKEN).capture()
            http_server.mode = (503, "CAPTURE_FAILED")
            source = _source(url=http_server.url, token=TOKEN)
            with pytest.raises(CameraScreenshotHttpError):
                source.capture()
        assert TOKEN not in caplog.text

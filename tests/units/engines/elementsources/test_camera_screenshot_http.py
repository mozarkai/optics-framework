"""CameraScreenshot over HTTP + bearer token, raw TCP, and env fallback."""
import json
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest

from optics_framework.engines.elementsources.camera_screenshot import (
    CameraScreenshot,
    CameraScreenshotHttpError,
    CapabilitiesConfig,
)

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
    """Fake controller: records requests, answers per `server.mode`."""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            received.append((self.path, self.headers.get("Authorization")))
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                return self._json(401, "UNAUTHORIZED", "bad token")
            if server.mode == "ok":
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(JPEG)))
                self.end_headers()
                self.wfile.write(JPEG)
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
    server.received = received
    server.url = f"http://127.0.0.1:{server.server_port}/screenshot"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def tcp_server():
    """Fake legacy server: answers `screenshot\\n` with 8-byte LE size + JPEG."""
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
    def test_success_sends_bearer_and_decodes(self, http_server):
        source = _source(url=http_server.url, token=TOKEN)
        frame = source.capture()
        assert frame.shape == (20, 30, 3)
        assert http_server.received == [("/screenshot", f"Bearer {TOKEN}")]

    @pytest.mark.parametrize(
        "status,code", [(404, "NOT_FOUND"), (503, "CAPTURE_FAILED"), (503, "SHUTTING_DOWN")]
    )
    def test_error_codes_are_surfaced(self, http_server, status, code):
        http_server.mode = (status, code)
        source = _source(url=http_server.url, token=TOKEN)
        with pytest.raises(CameraScreenshotHttpError) as exc:
            source.capture()
        assert exc.value.status == status
        assert exc.value.code == code
        assert f"HTTP {status}" in str(exc.value)
        assert f"{code} happened" in str(exc.value)
        assert TOKEN not in str(exc.value)

    def test_wrong_token_is_401(self, http_server):
        source = _source(url=http_server.url, token="wrong")
        with pytest.raises(CameraScreenshotHttpError) as exc:
            source.capture()
        assert exc.value.status == 401
        assert exc.value.code == "UNAUTHORIZED"
        assert "wrong" not in str(exc.value)

    def test_non_json_error_body(self, http_server):
        http_server.mode = "plain"
        with pytest.raises(CameraScreenshotHttpError) as exc:
            _source(url=http_server.url, token=TOKEN).capture()
        assert exc.value.status == 500
        assert "boom" in str(exc.value)

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
        with pytest.raises(RuntimeError, match="failed"):
            _source(url=url, token=TOKEN).capture()


class TestTcpUnchanged:
    def test_ip_port_uses_raw_tcp(self, tcp_server):
        port, commands = tcp_server
        source = _source(url=f"127.0.0.1:{port}")
        assert source.http_url is None
        frame = source.capture()
        assert frame.shape == (20, 30, 3)
        assert commands == [b"screenshot\n"]


class TestEnvFallback:
    def test_url_and_token_from_env(self, http_server, monkeypatch):
        monkeypatch.setenv("WEBCAM_SCREENSHOT_URL", http_server.url)
        monkeypatch.setenv("WEBCAM_SCREENSHOT_TOKEN", TOKEN)
        frame = CameraScreenshot({"capabilities": {}}).capture()
        assert frame.shape == (20, 30, 3)
        assert http_server.received[0][1] == f"Bearer {TOKEN}"

    def test_token_from_env_when_url_in_config(self, http_server, monkeypatch):
        monkeypatch.setenv("WEBCAM_SCREENSHOT_TOKEN", TOKEN)
        _source(url=http_server.url).capture()
        assert http_server.received[0][1] == f"Bearer {TOKEN}"

    def test_tcp_port_from_env(self, tcp_server, monkeypatch):
        port, commands = tcp_server
        monkeypatch.setenv("WEBCAM_SCREENSHOT_TCP_PORT", str(port))
        source = CameraScreenshot({"capabilities": {}})
        assert source.http_url is None
        assert source.capture().shape == (20, 30, 3)
        assert commands == [b"screenshot\n"]

    def test_http_url_beats_tcp_port_in_env(self, http_server, monkeypatch):
        monkeypatch.setenv("WEBCAM_SCREENSHOT_URL", http_server.url)
        monkeypatch.setenv("WEBCAM_SCREENSHOT_TOKEN", TOKEN)
        monkeypatch.setenv("WEBCAM_SCREENSHOT_TCP_PORT", "1")
        assert CameraScreenshot({"capabilities": {}}).http_url == http_server.url

    def test_config_url_beats_env_url(self, http_server, monkeypatch):
        monkeypatch.setenv("WEBCAM_SCREENSHOT_URL", "http://127.0.0.1:1/screenshot")
        monkeypatch.setenv("WEBCAM_SCREENSHOT_TOKEN", "env-token")
        _source(url=http_server.url, token=TOKEN).capture()
        assert http_server.received == [("/screenshot", f"Bearer {TOKEN}")]

    def test_config_tcp_url_beats_env_http(self, tcp_server, monkeypatch):
        port, _ = tcp_server
        monkeypatch.setenv("WEBCAM_SCREENSHOT_URL", "http://127.0.0.1:1/screenshot")
        source = _source(url=f"127.0.0.1:{port}")
        assert source.http_url is None
        assert source.capture().shape == (20, 30, 3)

    def test_camera_index_beats_env(self, monkeypatch):
        monkeypatch.setenv("WEBCAM_SCREENSHOT_URL", "http://127.0.0.1:1/screenshot")
        monkeypatch.setattr(cv2, "VideoCapture", lambda index: MagicMock(isOpened=lambda: False))
        source = _source(camera_index=0)
        assert source.http_url is None
        assert source.camera_index == 0

    def test_nothing_configured_still_raises(self):
        with pytest.raises(ValueError):
            CameraScreenshot({"capabilities": {}})


class TestTokenNotLogged:
    def test_model_masks_token(self):
        model = CapabilitiesConfig(url="http://h/screenshot", token=TOKEN)
        assert TOKEN not in repr(model)
        assert TOKEN not in str(model)
        assert TOKEN not in str(model.model_dump())
        assert TOKEN not in model.model_dump_json()

    def test_no_log_line_contains_token(self, http_server, caplog):
        with caplog.at_level(logging.DEBUG):
            _source(url=http_server.url, token=TOKEN).capture()
            http_server.mode = (503, "CAPTURE_FAILED")
            with pytest.raises(CameraScreenshotHttpError):
                _source(url=http_server.url, token=TOKEN).capture()
        assert TOKEN not in caplog.text

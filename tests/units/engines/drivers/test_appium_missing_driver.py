"""A server that lacks the driver the capabilities ask for: the session error names that server and why."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from optics_framework.common.config_handler import Config, ConfigHandler
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.eventSDK import EventSDK
from optics_framework.engines.drivers.appium import Appium

pytestmark = pytest.mark.black_box

APPIUM_3_9_NO_DRIVER = (
    "Could not find a driver for automationName 'Xa11y' and platformName 'Mac'. Have you installed a driver "
    "that supports those capabilities? Run 'appium driver list --installed' to see. "
    "(Lower-level error: Could not find installed driver to support given caps)"
)


class _AppiumWithoutXa11y(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = {"value": {"error": "session not created", "message": APPIUM_3_9_NO_DRIVER, "stacktrace": ""}}
        payload = json.dumps(body).encode()
        self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def server_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AppiumWithoutXa11y)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_a_server_without_the_driver_is_named_with_its_likely_causes(server_url):
    capabilities = {"platformName": "Mac", "automationName": "Xa11y", "app": "com.apple.calculator"}
    driver = Appium({"url": server_url, "capabilities": capabilities}, event_sdk=EventSDK(ConfigHandler(Config())))
    with pytest.raises(OpticsError) as failure:
        driver.start_session()
    assert failure.value.code == Code.E0102
    assert f"The Appium server at {server_url} has no driver for automationName 'Xa11y'" in failure.value.message
    assert "APPIUM_HOME" in failure.value.message
    assert "another Appium server" in failure.value.message
    assert APPIUM_3_9_NO_DRIVER in failure.value.message

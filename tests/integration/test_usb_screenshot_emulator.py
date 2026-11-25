import os
import subprocess  # nosec
import time

import pytest

from optics_framework.common.factories import TextFactory
from optics_framework.common.image_decode import decode_image
from optics_framework.engines.elementsources.usb_screenshot import UsbScreenshot
from optics_framework.engines.vision_models.ocr_models import installed_local_engines

pytestmark = pytest.mark.black_box


def test_a_usb_screenshot_of_the_emulator_shows_settings_to_ocr():
    serial = os.environ.get("OPTICS_E2E_EMULATOR")
    if not serial:
        pytest.skip("Set OPTICS_E2E_EMULATOR to an emulator's serial; the test opens its Settings app")
    engines = installed_local_engines()
    if not engines:
        pytest.skip("needs a local OCR engine: optics setup --install <engine>")
    subprocess.run(["adb", "-s", serial, "shell", "am", "start", "-W", "-a", "android.settings.SETTINGS"], check=True, capture_output=True)  # nosec
    source = UsbScreenshot({"capabilities": {"serial": serial, "platform": "android"}})
    width, height = (int(side) for side in subprocess.run(  # nosec
        ["adb", "-s", serial, "shell", "wm", "size"], check=True, capture_output=True, text=True
    ).stdout.split()[-1].split("x"))

    ocr = TextFactory.get_driver([{engines[0]: {"enabled": True}}])

    deadline, found = time.monotonic() + 15, None
    while found is None and time.monotonic() < deadline:
        frame = decode_image(source.capture_screenshot_bytes())
        found = ocr.find_element(frame, "Settings")
        if found is None:
            time.sleep(0.5)
    assert frame.shape[:2] == (height, width)
    assert found is not None

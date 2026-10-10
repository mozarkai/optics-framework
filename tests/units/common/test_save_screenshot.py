"""save_screenshot file names -- regression cover for screenshots named after
get_timestamp()'s ISO 8601 form (``2026-10-10T11:44:19.932343+05:30``), whose colons
Windows rejects and actions/upload-artifact refuses to upload.
"""
from datetime import datetime

import numpy as np
import pytest

from optics_framework.common import utils

pytestmark = pytest.mark.white_box


def _blank():
    return np.zeros((4, 4, 3), dtype=np.uint8)


def test_iso_timestamp_is_written_without_colons(tmp_path):
    utils.save_screenshot(
        _blank(), "assert_elements", str(tmp_path),
        time_stamp="2026-10-10T11:44:19.932343+05:30",
    )

    assert [p.name for p in tmp_path.iterdir()] == [
        "2026-10-10T11-44-19.932343+05-30-assert_elements.jpg"
    ]


def test_get_timestamp_output_makes_a_portable_file_name(tmp_path):
    utils.save_screenshot(_blank(), "press_element", str(tmp_path), time_stamp=utils.get_timestamp())

    (saved,) = tmp_path.iterdir()
    assert ":" not in saved.name


def test_default_timestamp_is_unchanged(tmp_path, monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 10, 11, 44, 19, 932343)

    monkeypatch.setattr(utils, "datetime", FrozenDatetime)

    utils.save_screenshot(_blank(), "capture_screenshot", str(tmp_path))

    assert [p.name for p in tmp_path.iterdir()] == [
        "2026-10-10T11-44-19-932343-capture_screenshot.jpg"
    ]

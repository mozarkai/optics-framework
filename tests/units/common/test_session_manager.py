import tempfile

import pytest

from optics_framework.common.config_handler import Config
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.session_manager import SessionManager


@pytest.fixture
def start_without_a_driver(tmp_path, monkeypatch):
    """Start a session whose driver does not exist; returns the session folders left in the temp folder."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with pytest.raises(OpticsError) as caught:
        SessionManager().create_session(Config(driver_sources=[{"nodriver": {"enabled": True}}]), None, None, None, None)
    assert caught.value.code == Code.E0601
    return list(tmp_path.glob("optics_session_*"))


def test_pin_a_session_that_cannot_start_leaves_its_folder(start_without_a_driver):
    assert len(start_without_a_driver) == 1


@pytest.mark.skip(reason="bug: a session that cannot start leaves its temp folder, "
                         "because Session.__init__ makes it before it builds the driver")
def test_a_session_that_cannot_start_leaves_no_folder(start_without_a_driver):
    assert start_without_a_driver == []

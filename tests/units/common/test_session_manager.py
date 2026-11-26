import tempfile

import pytest

from optics_framework.common.config_handler import Config
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.session_manager import SessionManager


def test_a_session_that_cannot_start_leaves_nothing_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "temp"))
    (tmp_path / "temp").mkdir()
    output = str(tmp_path / "output")
    manager = SessionManager()
    config = Config(driver_sources=[{"nodriver": {"enabled": True}}], execution_output_path=output)
    with pytest.raises(OpticsError) as caught:
        manager.create_session(config, None, None, None, None)
    assert caught.value.code == Code.E0601
    assert list((tmp_path / "temp").iterdir()) == []

    session_id = manager.create_session(Config(execution_output_path=output), None, None, None, None)
    manager.terminate_session(session_id)
    assert sorted(path.name for path in (tmp_path / "output").glob("junit_output*.xml")) == ["junit_output.xml"]

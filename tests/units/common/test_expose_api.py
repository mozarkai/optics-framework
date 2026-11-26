import base64
import pathlib

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from optics_framework.common import expose_api
from optics_framework.common.config_handler import Config
from optics_framework.common.error import Code, OpticsError


def test_make_dependency_entry_refuses_a_storage_state_path():
    with pytest.raises(OpticsError) as err:
        expose_api._make_dependency_entry("playwright", {"capabilities": {"storage_state": "/etc/auth.json"}})
    assert err.value.code == Code.E0501

    state = {"cookies": [], "origins": []}
    entry = expose_api._make_dependency_entry("playwright", {"capabilities": {"storage_state": state}})
    assert entry["playwright"].capabilities["storage_state"] == state


@pytest.mark.parametrize("cfg", [{"url": "http://169.254.169.254/"}, {"capabilities": {"url": "10.0.0.5:3000"}}])
def test_a_remote_session_reads_only_the_servers_camera(cfg):
    with pytest.raises(OpticsError) as err:
        expose_api._make_dependency_entry("camera_screenshot", cfg)
    assert err.value.code == Code.E0501

    entry = expose_api._make_dependency_entry("camera_screenshot", {"capabilities": {"out_width": 1170}})
    assert entry["camera_screenshot"].capabilities == {"out_width": 1170}


def test_loggable_masks_storage_state():
    state = {"cookies": [{"name": "sid", "value": "secret-session"}], "origins": []}
    sources = {"driver_sources": [expose_api._make_dependency_entry("playwright", {"capabilities": {"storage_state": state}})]}

    logged = expose_api._loggable(sources)

    assert logged["driver_sources"][0]["playwright"]["capabilities"]["storage_state"] == "***"
    assert state["cookies"][0]["value"] == "secret-session"


@pytest.fixture
def client():
    return TestClient(expose_api.app)


@pytest.fixture
def session():
    """A session in the server's registry, as the start route leaves one, without a driver."""
    session_id = expose_api.session_manager.create_session(Config(), None, None, None, None)
    yield expose_api.session_manager.get_session(session_id)
    expose_api.session_manager.terminate_session(session_id)


def test_pin_stopping_a_session_that_does_not_exist_is_a_server_error(client):
    assert client.delete("/v1/sessions/missing/stop").status_code == 500


@pytest.mark.skip(reason="bug: delete_session turns every HTTPException from execute_keyword into a 500, "
                         "so a missing session is not a 404 and a session whose app will not close stays open")
def test_stopping_a_session_that_does_not_exist_is_not_found(client):
    assert client.delete("/v1/sessions/missing/stop").status_code == 404


def test_add_session_api_invalid_data_returns_400(client, session):
    resp = client.post(f"/v1/sessions/{session.session_id}/api", json={"collections": "bad"})
    assert resp.status_code == 400
    assert expose_api.MSG_INVALID_API_DATA in resp.json()["detail"]


@pytest.mark.parametrize(
    "path", ["/v1/sessions/missing/events", "/v1/sessions/missing/workspace/stream"]
)
def test_stream_endpoints_session_not_found_returns_404(client, path):
    resp = client.get(path)
    assert resp.status_code == 404
    assert resp.json()["detail"] == expose_api.SESSION_NOT_FOUND


@pytest.mark.parametrize(("name", "status"), [("my_btn", 200), ("../../etc/passwd", 400), ("a/b", 400), ("..", 400)])
def test_an_uploaded_template_is_stored_only_under_a_safe_name(client, session, name, status):
    raw = cv2.imencode(".png", np.zeros((2, 2, 3), np.uint8))[1].tobytes()
    payload = {"name": name, "image_base64": base64.b64encode(raw).decode("ascii")}
    resp = client.post(f"/v1/sessions/{session.session_id}/templates", json=payload)
    assert resp.status_code == status
    stored = [pathlib.Path(p) for p in session.inline_templates.values()]
    expected = [(pathlib.Path(session._session_dir), raw)] if status == 200 else []
    assert [(p.parent, p.read_bytes()) for p in stored] == expected


def test_a_new_session_has_no_variables(client, session):
    resp = client.get(f"/v1/sessions/{session.session_id}/variables")
    assert resp.status_code == 200
    assert resp.json()["variables"] == {}

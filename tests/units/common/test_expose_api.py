import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from optics_framework.common import expose_api
from optics_framework.common.error import Code, OpticsError


def _run(coro):
    return asyncio.run(coro)


def test_make_dependency_entry_refuses_a_storage_state_path():
    with pytest.raises(OpticsError) as err:
        expose_api._make_dependency_entry("playwright", {"capabilities": {"storage_state": "/etc/auth.json"}})
    assert err.value.code == Code.E0501

    state = {"cookies": [], "origins": []}
    entry = expose_api._make_dependency_entry("playwright", {"capabilities": {"storage_state": state}})
    assert entry["playwright"].capabilities["storage_state"] == state


def test_loggable_masks_storage_state():
    state = {"cookies": [{"name": "sid", "value": "secret-session"}], "origins": []}
    sources = {"driver_sources": [expose_api._make_dependency_entry("playwright", {"capabilities": {"storage_state": state}})]}

    logged = expose_api._loggable(sources)

    assert logged["driver_sources"][0]["playwright"]["capabilities"]["storage_state"] == "***"
    assert state["cookies"][0]["value"] == "secret-session"


def test_normalize_param_value_list_passthrough():
    assert expose_api._normalize_param_value("p", ["a", "b"]) == ["a", "b"]


@pytest.fixture
def client():
    return TestClient(expose_api.app)


def test_list_keywords_endpoint(client):
    resp = client.get("/v1/keywords")
    assert resp.status_code == 200
    slugs = {k["keyword_slug"] for k in resp.json()}
    assert "press_element" in slugs


def test_delete_session_optics_error_propagates_status(client):
    err = OpticsError(Code.E0402, message="boom")
    with patch.object(expose_api, "execute_keyword", new=AsyncMock(side_effect=err)):
        resp = client.delete("/v1/sessions/s1/stop")
    assert resp.status_code == 404


def test_add_session_api_invalid_data_returns_400(client):
    session = MagicMock()
    with patch.object(expose_api.session_manager, "get_session", return_value=session):
        resp = client.post("/v1/sessions/s1/api", json={"collections": "bad"})
    assert resp.status_code == 400
    assert expose_api.MSG_INVALID_API_DATA in resp.json()["detail"]


@pytest.mark.parametrize(
    "path", ["/v1/sessions/missing/events", "/v1/sessions/missing/workspace/stream"]
)
def test_stream_endpoints_session_not_found_returns_404(client, path):
    with patch.object(expose_api.session_manager, "get_session", return_value=None):
        resp = client.get(path)
    assert resp.status_code == 404
    assert resp.json()["detail"] == expose_api.SESSION_NOT_FOUND


def test_empty_workspace_data_with_source():
    data = expose_api._empty_workspace_data(include_source=True)
    assert data[expose_api.KEY_SOURCE] == ""


def test_compute_workspace_hash_includes_source_when_present():
    base = {expose_api.KEY_SCREENSHOT: "img", expose_api.KEY_ELEMENTS: []}
    with_source = {**base, expose_api.KEY_SOURCE: "<xml/>"}
    assert expose_api._compute_workspace_hash(base) != expose_api._compute_workspace_hash(with_source)


def test_upload_template_success_writes_file_and_registers(client, tmp_path):
    session = MagicMock()
    session._session_dir = str(tmp_path / "inline")
    session.inline_templates = {}
    raw = cv2.imencode(".png", np.zeros((2, 2, 3), np.uint8))[1].tobytes()
    payload = {"name": "my_btn", "image_base64": base64.b64encode(raw).decode("ascii")}
    with patch.object(expose_api.session_manager, "get_session", return_value=session):
        resp = client.post("/v1/sessions/s1/templates", json=payload)
    assert resp.status_code == 200
    assert resp.json() == {"name": "my_btn", "status": expose_api.STATUS_OK}
    # The logical name now maps to a file on disk containing the decoded bytes.
    stored_path = session.inline_templates["my_btn"]
    with open(stored_path, "rb") as f:
        assert f.read() == raw


def test_get_session_variables_empty_when_store_absent(client):
    with patch.object(expose_api.session_manager, "get_session", return_value=SimpleNamespace(elements=None)):
        resp = client.get("/v1/sessions/s1/variables")

    assert resp.status_code == 200
    assert resp.json()["variables"] == {}

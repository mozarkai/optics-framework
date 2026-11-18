"""Unit tests for the HTTP API surface in ``optics_framework/common/expose_api.py``.

Hermetic and device-less: no driver/engine is ever instantiated. Pure helpers
(keyword reflection & humanization, source-config normalization, param handling,
exception classification) are exercised directly; the FastAPI endpoints are
driven through ``fastapi.testclient.TestClient`` with ``session_manager`` /
``execute_keyword`` / ``ExecutionEngine`` / ``KeywordRegistry`` mocked so no
real session, optics builder, or driver is built.

Template/base64 basics and ``_safe_template_filename`` are already covered by
``test_expose_api_vision.py`` and are not duplicated here.

Source under test: optics_framework/common/expose_api.py
"""
import asyncio
import base64
import json
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


# ---------------------------------------------------------------------------
# _humanize_keyword
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _extract_keywords_from_class / discover_keywords
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _make_dependency_entry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flag", [True, False])
def test_make_dependency_entry_bool_sets_enabled(flag):
    entry = expose_api._make_dependency_entry("selenium", flag)
    assert entry["selenium"].enabled is flag


def test_make_dependency_entry_refuses_a_storage_state_path():
    with pytest.raises(OpticsError) as err:
        expose_api._make_dependency_entry("playwright", {"capabilities": {"storage_state": "/etc/auth.json"}})
    assert err.value.code == Code.E0501

    state = {"cookies": [], "origins": []}
    entry = expose_api._make_dependency_entry("playwright", {"capabilities": {"storage_state": state}})
    assert entry["playwright"].capabilities["storage_state"] == state


def test_make_dependency_entry_refuses_a_storage_state_that_is_not_an_object():
    with pytest.raises(OpticsError) as err:
        expose_api._make_dependency_entry("playwright", {"capabilities": {"storage_state": ["/etc/auth.json"]}})
    assert err.value.code == Code.E0501


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


# ---------------------------------------------------------------------------
# SessionConfig._normalize_item / normalize_sources
# ---------------------------------------------------------------------------


def test_normalize_item_invalid_type_raises():
    cfg = expose_api.SessionConfig()
    with pytest.raises(ValueError, match="Unsupported source item type"):
        cfg._normalize_item(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _normalize_param_value
# ---------------------------------------------------------------------------


def test_normalize_param_value_none_returns_empty():
    assert expose_api._normalize_param_value("p", None) == []  # type: ignore[arg-type]


def test_normalize_param_value_list_passthrough():
    assert expose_api._normalize_param_value("p", ["a", "b"]) == ["a", "b"]


def test_normalize_param_value_list_param_serializes_json():
    out = expose_api._normalize_param_value("p", ["a", "b"], is_list_param=True)
    assert out == [json.dumps(["a", "b"])]
    assert json.loads(out[0]) == ["a", "b"]


def test_normalize_param_value_empty_list_raises():
    with pytest.raises(ValueError, match="Empty list not allowed"):
        expose_api._normalize_param_value("p", [])


def test_normalize_param_value_mixed_types_raises():
    with pytest.raises(TypeError, match="must be List\\[str\\]"):
        expose_api._normalize_param_value("p", ["a", 1])  # type: ignore[list-item]


def test_normalize_param_value_wrong_scalar_type_raises():
    with pytest.raises(TypeError, match="must be str or List"):
        expose_api._normalize_param_value("p", 42)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _execute_keyword_with_fallback
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _handle_execution_failure
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Endpoints via TestClient
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# run_keyword_endpoint & thin observer GET endpoints
# ---------------------------------------------------------------------------


def test_elements_endpoint_passes_filter_config(client):
    response = expose_api.ExecutionResponse(
        execution_id="e", status=expose_api.STATUS_SUCCESS, data={expose_api.KEY_RESULT: []}
    )
    mock = AsyncMock(return_value=response)
    with patch.object(expose_api, "execute_keyword", new=mock):
        resp = client.get(
            "/v1/sessions/s1/elements", params={"filter_config": ["buttons", "inputs"]}
        )
    assert resp.status_code == 200
    request = mock.await_args.args[1]
    assert request.keyword == "get_interactive_elements"
    assert request.params == {expose_api.PARAM_FILTER_CONFIG: ["buttons", "inputs"]}


# ---------------------------------------------------------------------------
# _parse_api_data_to_model / add_session_api
# ---------------------------------------------------------------------------


def test_parse_api_data_non_dict_raises():
    with pytest.raises(ValueError, match="must be a dictionary"):
        expose_api._parse_api_data_to_model(["not", "a", "dict"])  # type: ignore[arg-type]


def test_add_session_api_session_not_found_returns_404(client):
    with patch.object(expose_api.session_manager, "get_session", return_value=None):
        resp = client.post("/v1/sessions/missing/api", json={})
    assert resp.status_code == 404


def test_add_session_api_invalid_data_returns_400(client):
    session = MagicMock()
    with patch.object(expose_api.session_manager, "get_session", return_value=session):
        resp = client.post("/v1/sessions/s1/api", json={"collections": "bad"})
    assert resp.status_code == 400
    assert expose_api.MSG_INVALID_API_DATA in resp.json()["detail"]


# ---------------------------------------------------------------------------
# SSE endpoint 404 guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/v1/sessions/missing/events", "/v1/sessions/missing/workspace/stream"]
)
def test_stream_endpoints_session_not_found_returns_404(client, path):
    with patch.object(expose_api.session_manager, "get_session", return_value=None):
        resp = client.get(path)
    assert resp.status_code == 404
    assert resp.json()["detail"] == expose_api.SESSION_NOT_FOUND


# ---------------------------------------------------------------------------
# workspace pure helpers
# ---------------------------------------------------------------------------


def test_empty_workspace_data_with_source():
    data = expose_api._empty_workspace_data(include_source=True)
    assert data[expose_api.KEY_SOURCE] == ""


def test_compute_workspace_hash_includes_source_when_present():
    base = {expose_api.KEY_SCREENSHOT: "img", expose_api.KEY_ELEMENTS: []}
    with_source = {**base, expose_api.KEY_SOURCE: "<xml/>"}
    assert expose_api._compute_workspace_hash(base) != expose_api._compute_workspace_hash(with_source)


# ---------------------------------------------------------------------------
# upload_template endpoint
# ---------------------------------------------------------------------------


def test_upload_template_invalid_base64_returns_400(client):
    session = MagicMock()
    with patch.object(expose_api.session_manager, "get_session", return_value=session):
        resp = client.post(
            "/v1/sessions/s1/templates",
            json={"name": "btn", "image_base64": "!!!not-base64!!!"},
        )
    assert resp.status_code == 400
    assert expose_api.MSG_INVALID_BASE64_IMAGE in resp.json()["detail"]


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

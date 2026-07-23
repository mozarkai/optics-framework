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
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from optics_framework.common import expose_api
from optics_framework.common.config_handler import DependencyConfig
from optics_framework.common.error import Code, OpticsError


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# _humanize_keyword
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("press_element", "Press Element"),
        ("get_driver_session_id", "Get Driver Session Id"),
        ("enter_text", "Enter Text"),
        ("swipe", "Swipe"),
        ("__leading__underscores__", "Leading Underscores"),
        ("multiple___underscores", "Multiple Underscores"),
    ],
)
def test_humanize_keyword(name, expected):
    assert expose_api._humanize_keyword(name) == expected


# ---------------------------------------------------------------------------
# _extract_keywords_from_class / discover_keywords
# ---------------------------------------------------------------------------


class _SampleApi:
    """Fixture class emulating an API keyword class."""

    def press_button(self, element: str, repeat: int = 1):
        """Press a button."""

    def get_value(self):
        # no docstring on purpose
        return None

    def _private_helper(self):  # excluded (leading underscore)
        pass

    def test_scaffold(self):  # excluded (test-prefixed)
        pass


def test_extract_keywords_skips_private_and_test_methods():
    infos = expose_api._extract_keywords_from_class(_SampleApi)
    slugs = {i.keyword_slug for i in infos}
    assert slugs == {"press_button", "get_value"}
    assert "_private_helper" not in slugs
    assert "test_scaffold" not in slugs


def test_extract_keywords_humanizes_and_captures_params():
    infos = {i.keyword_slug: i for i in expose_api._extract_keywords_from_class(_SampleApi)}
    press = infos["press_button"]
    assert press.keyword == "Press Button"
    assert press.description == "Press a button."
    pnames = {p.name for p in press.parameters}
    # "self" is dropped; declared params are captured.
    assert "self" not in pnames
    assert {"element", "repeat"} <= pnames
    repeat = next(p for p in press.parameters if p.name == "repeat")
    assert repeat.default == 1
    # Method without a docstring yields an empty description string.
    assert infos["get_value"].description == ""


def test_discover_keywords_finds_real_api_keywords():
    infos = expose_api.discover_keywords()
    slugs = {i.keyword_slug for i in infos}
    # Well-known keywords defined across optics_framework.api.* classes.
    assert {"press_element", "capture_screenshot", "enter_text"} <= slugs
    # Reflection never leaks dunder/private/test methods.
    assert not any(s.startswith("_") or s.startswith("test") for s in slugs)


# ---------------------------------------------------------------------------
# _make_dependency_entry
# ---------------------------------------------------------------------------


def test_make_dependency_entry_none_defaults_enabled():
    entry = expose_api._make_dependency_entry("easyocr", None)
    cfg = entry["easyocr"]
    assert isinstance(cfg, DependencyConfig)
    assert cfg.enabled is True
    assert cfg.url is None
    assert cfg.capabilities == {}


@pytest.mark.parametrize("flag", [True, False])
def test_make_dependency_entry_bool_sets_enabled(flag):
    entry = expose_api._make_dependency_entry("selenium", flag)
    assert entry["selenium"].enabled is flag


def test_make_dependency_entry_dict_reads_all_fields():
    cfg_in = {"enabled": False, "url": "http://x", "capabilities": {"a": 1}}
    entry = expose_api._make_dependency_entry("appium", cfg_in)
    cfg = entry["appium"]
    assert cfg.enabled is False
    assert cfg.url == "http://x"
    assert cfg.capabilities == {"a": 1}


def test_make_dependency_entry_appium_uses_top_level_defaults():
    entry = expose_api._make_dependency_entry(
        "appium", None, top_level_url="http://hub", top_level_capabilities={"k": "v"}
    )
    cfg = entry["appium"]
    assert cfg.url == "http://hub"
    assert cfg.capabilities == {"k": "v"}


def test_make_dependency_entry_top_level_url_ignored_for_non_appium():
    entry = expose_api._make_dependency_entry("selenium", None, top_level_url="http://hub")
    assert entry["selenium"].url is None


def test_make_dependency_entry_dict_falls_back_to_top_level_url_for_appium():
    entry = expose_api._make_dependency_entry(
        "appium", {"enabled": True}, top_level_url="http://hub"
    )
    assert entry["appium"].url == "http://hub"


# ---------------------------------------------------------------------------
# SessionConfig._normalize_item / normalize_sources
# ---------------------------------------------------------------------------


def test_normalize_item_string_non_appium():
    cfg = expose_api.SessionConfig()
    out = cfg._normalize_item("selenium")
    assert set(out) == {"selenium"}
    assert out["selenium"].enabled is True


def test_normalize_item_string_appium_prefers_top_level():
    cfg = expose_api.SessionConfig()
    out = cfg._normalize_item(
        "appium", top_level_url="http://hub", top_level_capabilities={"c": 1}
    )
    assert out["appium"].url == "http://hub"
    assert out["appium"].capabilities == {"c": 1}


def test_normalize_item_dict():
    cfg = expose_api.SessionConfig()
    out = cfg._normalize_item({"appium": {"enabled": False, "url": "http://z"}})
    assert out["appium"].enabled is False
    assert out["appium"].url == "http://z"


def test_normalize_item_invalid_type_raises():
    cfg = expose_api.SessionConfig()
    with pytest.raises(ValueError, match="Unsupported source item type"):
        cfg._normalize_item(123)  # type: ignore[arg-type]


def test_normalize_sources_maps_all_buckets_and_injects_appium_top_level():
    cfg = expose_api.SessionConfig(
        driver_sources=["appium"],
        elements_sources=["appium_find_element"],
        text_detection=[{"easyocr": {"enabled": True}}],
        image_detection=["templatematch"],
        appium_url="http://hub:4723",
        appium_config={"platformName": "Android"},
    )
    normalized = cfg.normalize_sources()
    assert set(normalized) == {
        expose_api.KEY_DRIVER_SOURCES,
        expose_api.KEY_ELEMENTS_SOURCES,
        expose_api.KEY_TEXT_DETECTION,
        expose_api.KEY_IMAGE_DETECTION,
    }
    # appium in driver_sources picks up the top-level url/capabilities.
    appium_cfg = normalized[expose_api.KEY_DRIVER_SOURCES][0]["appium"]
    assert appium_cfg.url == "http://hub:4723"
    assert appium_cfg.capabilities == {"platformName": "Android"}
    # Non-driver buckets do NOT receive the top-level appium url.
    el_cfg = normalized[expose_api.KEY_ELEMENTS_SOURCES][0]["appium_find_element"]
    assert el_cfg.url is None
    assert normalized[expose_api.KEY_TEXT_DETECTION][0]["easyocr"].enabled is True


def test_normalize_sources_empty_lists():
    normalized = expose_api.SessionConfig().normalize_sources()
    assert all(v == [] for v in normalized.values())


# ---------------------------------------------------------------------------
# _normalize_param_value
# ---------------------------------------------------------------------------


def test_normalize_param_value_none_returns_empty():
    assert expose_api._normalize_param_value("p", None) == []  # type: ignore[arg-type]


def test_normalize_param_value_str_wraps():
    assert expose_api._normalize_param_value("p", "hello") == ["hello"]


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
# _resolve_named_to_positional
# ---------------------------------------------------------------------------


def test_resolve_named_to_positional_orders_by_signature():
    def method(self, first: str, second: str):
        pass

    out = expose_api._resolve_named_to_positional(
        method, {"second": "b", "first": "a"}
    )
    assert out == [["a"], ["b"]]


def test_resolve_named_to_positional_skips_defaulted_when_absent():
    def method(self, element: str, repeat: str = "1"):
        pass

    out = expose_api._resolve_named_to_positional(method, {"element": "x"})
    # Defaulted param omitted from the caller -> not included.
    assert out == [["x"]]


def test_resolve_named_to_positional_missing_required_raises():
    def method(self, element: str):
        pass

    with pytest.raises(ValueError, match="Required parameter 'element'"):
        expose_api._resolve_named_to_positional(method, {})


# ---------------------------------------------------------------------------
# _should_reraise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("exc", [SystemExit(), KeyboardInterrupt(), GeneratorExit()])
def test_should_reraise_true_for_control_flow(exc):
    assert expose_api._should_reraise(exc) is True


@pytest.mark.parametrize("exc", [ValueError("x"), RuntimeError("y"), OpticsError(Code.E0201)])
def test_should_reraise_false_for_regular(exc):
    assert expose_api._should_reraise(exc) is False


# ---------------------------------------------------------------------------
# _execute_keyword_with_fallback
# ---------------------------------------------------------------------------


def _dummy_method(self):
    pass


def test_fallback_no_params_calls_engine_once():
    engine = MagicMock()
    engine.execute = AsyncMock(return_value="RESULT")
    result = _run(
        expose_api._execute_keyword_with_fallback(
            engine, "sess", "capture_screenshot", [], _dummy_method, MagicMock()
        )
    )
    assert result == "RESULT"
    engine.execute.assert_awaited_once()


def test_fallback_positional_single_combo():
    engine = MagicMock()
    engine.execute = AsyncMock(return_value="ok")
    result = _run(
        expose_api._execute_keyword_with_fallback(
            engine, "sess", "press_element", ["Login"], _dummy_method, MagicMock()
        )
    )
    assert result == "ok"
    params = engine.execute.await_args.args[0].params
    assert params == ["Login"]


def test_fallback_positional_tries_next_value_on_failure():
    engine = MagicMock()
    engine.execute = AsyncMock(side_effect=[RuntimeError("nope"), "recovered"])
    result = _run(
        expose_api._execute_keyword_with_fallback(
            engine, "sess", "press_element", [["a", "b"]], _dummy_method, MagicMock()
        )
    )
    assert result == "recovered"
    assert engine.execute.await_count == 2


def test_fallback_positional_all_fail_raises_runtime_error():
    engine = MagicMock()
    engine.execute = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="All fallback attempts failed"):
        _run(
            expose_api._execute_keyword_with_fallback(
                engine, "sess", "press_element", [["a", "b"]], _dummy_method, MagicMock()
            )
        )
    assert engine.execute.await_count == 2


def test_fallback_named_params_map_to_positional():
    def method(self, element: str):
        pass

    engine = MagicMock()
    engine.execute = AsyncMock(return_value="named-ok")
    result = _run(
        expose_api._execute_keyword_with_fallback(
            engine, "sess", "press_element", {"element": "Login"}, method, MagicMock()
        )
    )
    assert result == "named-ok"
    assert engine.execute.await_args.args[0].params == ["Login"]


def test_fallback_named_params_iterate_fallback_list():
    def method(self, element: str):
        pass

    engine = MagicMock()
    engine.execute = AsyncMock(side_effect=[RuntimeError("x"), "second"])
    result = _run(
        expose_api._execute_keyword_with_fallback(
            engine, "sess", "press_element", {"element": ["a", "b"]}, method, MagicMock()
        )
    )
    assert result == "second"
    assert engine.execute.await_count == 2


def test_fallback_named_params_fold_in_defaults():
    def method(self, element: str, index: str = "0"):
        pass

    engine = MagicMock()
    engine.execute = AsyncMock(return_value="ok")
    result = _run(
        expose_api._execute_keyword_with_fallback(
            engine, "sess", "press_element", {"element": "x"}, method, MagicMock()
        )
    )
    assert result == "ok"
    # The defaulted param is folded into the positional args in signature order.
    assert engine.execute.await_args.args[0].params == ["x", "0"]


def test_fallback_no_params_wraps_engine_failure():
    engine = MagicMock()
    engine.execute = AsyncMock(side_effect=RuntimeError("driver down"))
    with pytest.raises(RuntimeError, match="Keyword execution failed"):
        _run(
            expose_api._execute_keyword_with_fallback(
                engine, "sess", "capture_screenshot", [], _dummy_method, MagicMock()
            )
        )


def test_fallback_reraises_control_flow_exception():
    engine = MagicMock()
    engine.execute = AsyncMock(side_effect=SystemExit())
    with pytest.raises(SystemExit):
        _run(
            expose_api._execute_keyword_with_fallback(
                engine, "sess", "kw", ["a"], _dummy_method, MagicMock()
            )
        )


# ---------------------------------------------------------------------------
# _handle_execution_failure
# ---------------------------------------------------------------------------


def _fake_session_with_queue():
    session = MagicMock()
    session.event_queue.put = AsyncMock()
    return session


def test_handle_execution_failure_optics_error_maps_status():
    session = _fake_session_with_queue()
    err = OpticsError(Code.E0402, message="Keyword X not found")
    with pytest.raises(HTTPException) as exc_info:
        _run(expose_api._handle_execution_failure(err, session, "eid", "X"))
    assert exc_info.value.status_code == 404  # E0402 default_status
    session.event_queue.put.assert_awaited_once()


def test_handle_execution_failure_generic_error_is_500():
    session = _fake_session_with_queue()
    with pytest.raises(HTTPException) as exc_info:
        _run(expose_api._handle_execution_failure(ValueError("bad"), session, "eid", "X"))
    assert exc_info.value.status_code == 500
    assert expose_api.MSG_EXECUTION_FAILED in exc_info.value.detail


# ---------------------------------------------------------------------------
# Endpoints via TestClient
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    return TestClient(expose_api.app)


def test_health_check_endpoint(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == expose_api.HEALTH_STATUS_RUNNING
    assert "version" in body


def test_list_keywords_endpoint(client):
    resp = client.get("/v1/keywords")
    assert resp.status_code == 200
    slugs = {k["keyword_slug"] for k in resp.json()}
    assert "press_element" in slugs


def test_execute_keyword_session_not_found_returns_404(client):
    with patch.object(expose_api.session_manager, "get_session", return_value=None):
        resp = client.post(
            "/v1/sessions/missing/action",
            json={"mode": "keyword", "keyword": "Press Element", "params": []},
        )
    assert resp.status_code == 404
    assert resp.json()["detail"] == expose_api.SESSION_NOT_FOUND


def test_execute_keyword_wrong_mode_returns_400(client):
    session = MagicMock()
    with patch.object(expose_api.session_manager, "get_session", return_value=session):
        resp = client.post(
            "/v1/sessions/s1/action",
            json={"mode": "batch", "keyword": "Press Element", "params": []},
        )
    assert resp.status_code == 400
    assert resp.json()["detail"] == expose_api.MSG_ONLY_KEYWORD_MODE_SUPPORTED


def _fake_action_session():
    session = MagicMock()
    session.event_queue.put = AsyncMock()
    session.request_template_overrides = {}
    session.optics.build = MagicMock(return_value=MagicMock())
    return session


class _FakeRegistry:
    """KeywordRegistry stand-in with a controllable keyword_map."""

    keyword_map: dict = {}

    def register(self, instance):  # no-op; endpoint calls this per API class
        pass


def test_execute_keyword_unknown_keyword_returns_404(client):
    session = _fake_action_session()
    fake_registry = _FakeRegistry()
    fake_registry.keyword_map = {}
    with patch.object(expose_api.session_manager, "get_session", return_value=session), \
            patch.object(expose_api, "KeywordRegistry", return_value=fake_registry), \
            patch.object(expose_api, "FlowControl", return_value=MagicMock()):
        resp = client.post(
            "/v1/sessions/s1/action",
            json={"mode": "keyword", "keyword": "No Such Keyword", "params": []},
        )
    # Unknown keyword -> OpticsError(E0402) -> HTTP 404.
    assert resp.status_code == 404
    # A FAIL event was queued before the error surfaced.
    session.event_queue.put.assert_awaited()


def test_execute_keyword_success(client):
    session = _fake_action_session()

    def noop():
        return None

    fake_registry = _FakeRegistry()
    fake_registry.keyword_map = {"noop": noop}

    engine = MagicMock()
    engine.execute = AsyncMock(return_value="engine-result")

    with patch.object(expose_api.session_manager, "get_session", return_value=session), \
            patch.object(expose_api, "KeywordRegistry", return_value=fake_registry), \
            patch.object(expose_api, "FlowControl", return_value=MagicMock()), \
            patch.object(expose_api, "ExecutionEngine", return_value=engine):
        resp = client.post(
            "/v1/sessions/s1/action",
            json={"mode": "keyword", "keyword": "noop", "params": []},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == expose_api.STATUS_SUCCESS
    assert body["data"] == {expose_api.KEY_RESULT: "engine-result"}
    engine.execute.assert_awaited_once()


def test_create_session_success_and_deprecation(client):
    """create_session normalizes config, warns on legacy appium_* fields, and
    returns the driver id produced by the launch_app keyword call."""
    launch_response = expose_api.ExecutionResponse(
        execution_id="e1",
        status=expose_api.STATUS_SUCCESS,
        data={expose_api.KEY_RESULT: "driver-123"},
    )
    with patch.object(expose_api.session_manager, "create_session", return_value="sess-abc"), \
            patch.object(expose_api, "execute_keyword", new=AsyncMock(return_value=launch_response)), \
            patch.object(expose_api, "reconfigure_logging"), \
            pytest.warns(DeprecationWarning):
        resp = client.post(
            "/v1/sessions/start",
            json={
                "driver_sources": ["appium"],
                "appium_url": "http://hub:4723",
                "appium_config": {"platformName": "Android"},
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "sess-abc"
    assert body["driver_id"] == "driver-123"


def test_delete_session_success_and_hash_cleanup(client):
    expose_api.workspace_hashes["sess-del"] = "somehash"
    terminate = MagicMock()
    with patch.object(expose_api, "execute_keyword", new=AsyncMock(return_value=MagicMock())), \
            patch.object(expose_api.session_manager, "terminate_session", terminate):
        resp = client.delete("/v1/sessions/sess-del/stop")
    assert resp.status_code == 200
    assert resp.json()["status"] == expose_api.STATUS_TERMINATED
    terminate.assert_called_once_with("sess-del")
    # workspace hash entry is cleaned up to avoid a memory leak.
    assert "sess-del" not in expose_api.workspace_hashes


def test_delete_session_optics_error_propagates_status(client):
    err = OpticsError(Code.E0402, message="boom")
    with patch.object(expose_api, "execute_keyword", new=AsyncMock(side_effect=err)):
        resp = client.delete("/v1/sessions/s1/stop")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# run_keyword_endpoint & thin observer GET endpoints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected_keyword",
    [
        ("/v1/sessions/s1/screenshot", "capture_screenshot"),
        ("/v1/sessions/s1/driver-id", "get_driver_session_id"),
        ("/v1/sessions/s1/source", "capture_pagesource"),
        ("/v1/sessions/s1/screen_elements", "get_screen_elements"),
    ],
)
def test_observer_get_endpoints_delegate_to_run_keyword(client, path, expected_keyword):
    response = expose_api.ExecutionResponse(
        execution_id="e", status=expose_api.STATUS_SUCCESS, data={expose_api.KEY_RESULT: "v"}
    )
    mock = AsyncMock(return_value=response)
    with patch.object(expose_api, "execute_keyword", new=mock):
        resp = client.get(path)
    assert resp.status_code == 200
    # run_keyword_endpoint builds an ExecuteRequest around the fixed keyword.
    request = mock.await_args.args[1]
    assert request.keyword == expected_keyword
    assert request.mode == expose_api.MODE_KEYWORD


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


def test_elements_endpoint_without_filter_passes_no_params(client):
    response = expose_api.ExecutionResponse(
        execution_id="e", status=expose_api.STATUS_SUCCESS, data={expose_api.KEY_RESULT: []}
    )
    mock = AsyncMock(return_value=response)
    with patch.object(expose_api, "execute_keyword", new=mock):
        resp = client.get("/v1/sessions/s1/elements")
    assert resp.status_code == 200
    request = mock.await_args.args[1]
    # No filter -> run_keyword_endpoint's params default to [].
    assert request.params == []


# ---------------------------------------------------------------------------
# _parse_api_data_to_model / add_session_api
# ---------------------------------------------------------------------------


def test_parse_api_data_empty_dict_yields_empty_model():
    model = expose_api._parse_api_data_to_model({})
    assert model.collections == {}


def test_parse_api_data_unwraps_api_key():
    model = expose_api._parse_api_data_to_model({"api": {"global_defaults": {"x": 1}}})
    assert model.global_defaults == {"x": 1}


def test_parse_api_data_non_dict_raises():
    with pytest.raises(ValueError, match="must be a dictionary"):
        expose_api._parse_api_data_to_model(["not", "a", "dict"])  # type: ignore[arg-type]


def test_parse_api_data_validation_error_wrapped_as_value_error():
    with pytest.raises(ValueError):
        expose_api._parse_api_data_to_model({"collections": "not-a-mapping"})


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


def test_add_session_api_success_sets_session_apis(client):
    session = MagicMock()
    with patch.object(expose_api.session_manager, "get_session", return_value=session):
        resp = client.post("/v1/sessions/s1/api", json={"api": {"global_defaults": {"k": "v"}}})
    assert resp.status_code == 204
    assert session.apis.global_defaults == {"k": "v"}


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


def test_empty_workspace_data_without_source():
    data = expose_api._empty_workspace_data(include_source=False)
    assert data[expose_api.KEY_SCREENSHOT] == ""
    assert data[expose_api.KEY_ELEMENTS] == []
    assert data[expose_api.KEY_SCREENSHOT_FAILED] is True
    assert expose_api.KEY_SOURCE not in data


def test_empty_workspace_data_with_source():
    data = expose_api._empty_workspace_data(include_source=True)
    assert data[expose_api.KEY_SOURCE] == ""


def test_compute_workspace_hash_is_stable_and_change_sensitive():
    a = {expose_api.KEY_SCREENSHOT: "img", expose_api.KEY_ELEMENTS: [{"id": 1}]}
    b = {expose_api.KEY_SCREENSHOT: "img", expose_api.KEY_ELEMENTS: [{"id": 1}]}
    assert expose_api._compute_workspace_hash(a) == expose_api._compute_workspace_hash(b)
    c = {expose_api.KEY_SCREENSHOT: "different", expose_api.KEY_ELEMENTS: [{"id": 1}]}
    assert expose_api._compute_workspace_hash(a) != expose_api._compute_workspace_hash(c)


def test_compute_workspace_hash_includes_source_when_present():
    base = {expose_api.KEY_SCREENSHOT: "img", expose_api.KEY_ELEMENTS: []}
    with_source = {**base, expose_api.KEY_SOURCE: "<xml/>"}
    assert expose_api._compute_workspace_hash(base) != expose_api._compute_workspace_hash(with_source)


# ---------------------------------------------------------------------------
# upload_template endpoint
# ---------------------------------------------------------------------------


def test_upload_template_session_not_found_returns_404(client):
    with patch.object(expose_api.session_manager, "get_session", return_value=None):
        resp = client.post(
            "/v1/sessions/missing/templates",
            json={"name": "btn", "image_base64": "aGk="},
        )
    assert resp.status_code == 404


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
    import base64 as _b64

    session = MagicMock()
    session._inline_templates_dir = str(tmp_path / "inline")
    session.inline_templates = {}
    raw = b"\x89PNG\r\n\x1a\nfake"
    payload = {"name": "my_btn", "image_base64": _b64.b64encode(raw).decode("ascii")}
    with patch.object(expose_api.session_manager, "get_session", return_value=session):
        resp = client.post("/v1/sessions/s1/templates", json=payload)
    assert resp.status_code == 200
    assert resp.json() == {"name": "my_btn", "status": expose_api.STATUS_OK}
    # The logical name now maps to a file on disk containing the decoded bytes.
    stored_path = session.inline_templates["my_btn"]
    with open(stored_path, "rb") as f:
        assert f.read() == raw

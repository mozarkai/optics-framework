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
import threading
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
    create_thread_id: list[int] = []
    event_loop_thread_id: list[int] = []

    def create_session(*args, **kwargs):
        create_thread_id.append(threading.get_ident())
        return "sess-abc"

    async def launch_app(*args, **kwargs):
        event_loop_thread_id.append(threading.get_ident())
        return launch_response

    launch_response = expose_api.ExecutionResponse(
        execution_id="e1",
        status=expose_api.STATUS_SUCCESS,
        data={expose_api.KEY_RESULT: "driver-123"},
    )
    with patch.object(expose_api.session_manager, "create_session", side_effect=create_session), \
            patch.object(expose_api, "execute_keyword", side_effect=launch_app), \
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
    assert create_thread_id != event_loop_thread_id


def test_delete_session_success_and_hash_cleanup(client):
    expose_api.workspace_hashes["sess-del"] = "somehash"
    terminate = MagicMock()
    # I5: an unknown id is now a 404, so the session has to actually exist.
    with patch.object(expose_api.session_manager, "get_session",
                      return_value=_session_with_real_lock("sess-del")), \
         patch.object(expose_api.session_manager, "terminate_session", terminate):
        resp = client.delete("/v1/sessions/sess-del/stop")
    assert resp.status_code == 200
    assert resp.json()["status"] == expose_api.STATUS_TERMINATED
    terminate.assert_called_once_with("sess-del")
    # workspace hash entry is cleaned up to avoid a memory leak.
    assert "sess-del" not in expose_api.workspace_hashes


def test_delete_session_optics_error_propagates_status(client):
    # G1: delete_session no longer routes teardown through execute_keyword
    # (that close_and_terminate_app pre-call was removed), so the failure is
    # now injected directly into session_manager.terminate_session, the sole
    # remaining teardown path.
    err = OpticsError(Code.E0402, message="boom")
    with patch.object(expose_api.session_manager, "get_session",
                      return_value=_session_with_real_lock()), \
         patch.object(expose_api.session_manager, "terminate_session", side_effect=err):
        resp = client.delete("/v1/sessions/s1/stop")
    assert resp.status_code == 404
    assert resp.json()["detail"]["message"] == "boom"


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
    session = MagicMock()
    session._inline_templates_dir = str(tmp_path / "inline")
    session.inline_templates = {}
    raw = b"\x89PNG\r\n\x1a\nfake"
    payload = {"name": "my_btn", "image_base64": base64.b64encode(raw).decode("ascii")}
    with patch.object(expose_api.session_manager, "get_session", return_value=session):
        resp = client.post("/v1/sessions/s1/templates", json=payload)
    assert resp.status_code == 200
    assert resp.json() == {"name": "my_btn", "status": expose_api.STATUS_OK}
    # The logical name now maps to a file on disk containing the decoded bytes.
    stored_path = session.inline_templates["my_btn"]
    with open(stored_path, "rb") as f:
        assert f.read() == raw


def test_delete_session_evicts_even_when_driver_teardown_fails():
    """G1: a driver that refuses to quit must not make a session un-evictable."""
    mgr = MagicMock()
    mgr.get_session.return_value = _session_with_real_lock("sess-broken")
    # terminate_session is the sole remaining teardown path (the
    # close_and_terminate_app pre-call through execute_keyword was removed),
    # so it's the mock that must simulate the misbehaving driver.
    mgr.terminate_session = MagicMock(side_effect=RuntimeError("device rebooted"))

    with patch.object(expose_api, "session_manager", mgr):
        expose_api.workspace_hashes["sess-broken"] = "deadbeef"
        with pytest.raises(HTTPException) as exc:
            _run(expose_api.delete_session("sess-broken"))

    # The caller still learns it failed...
    assert exc.value.status_code == 500
    # ...but the session is gone regardless.
    mgr.terminate_session.assert_called_once_with("sess-broken")
    assert "sess-broken" not in expose_api.workspace_hashes


def _minimal_session_config():
    """Smallest SessionConfig that normalizes to one enabled driver source."""
    return expose_api.SessionConfig(
        driver_sources=[{"appium": {"enabled": True, "url": "http://127.0.0.1:4723"}}],
        elements_sources=[],
        text_detection=[],
        image_detection=[],
    )


def test_create_session_terminates_when_app_launch_fails():
    """G2: a failed auto-launch must not leave a registered session behind."""
    mgr = MagicMock()
    mgr.create_session = MagicMock(return_value="sess-half-built")
    mgr.terminate_session = MagicMock()

    with patch.object(expose_api, "session_manager", mgr), \
         patch.object(
             expose_api, "execute_keyword",
             AsyncMock(side_effect=RuntimeError("app not installed")),
         ), \
         patch.object(expose_api, "reconfigure_logging", MagicMock()):
        with pytest.raises(HTTPException):
            _run(expose_api.create_session(_minimal_session_config()))

    mgr.terminate_session.assert_called_once_with("sess-half-built")


def test_create_session_preserves_http_status_from_launch():
    """G2: an HTTPException from launch must not be flattened into a 500."""
    mgr = MagicMock()
    mgr.create_session = MagicMock(return_value="sess-1")
    mgr.terminate_session = MagicMock()

    with patch.object(expose_api, "session_manager", mgr), \
         patch.object(
             expose_api, "execute_keyword",
             AsyncMock(side_effect=HTTPException(status_code=404, detail="no such keyword")),
         ), \
         patch.object(expose_api, "reconfigure_logging", MagicMock()):
        with pytest.raises(HTTPException) as exc:
            _run(expose_api.create_session(_minimal_session_config()))

    assert exc.value.status_code == 404
    assert exc.value.detail == "no such keyword"
    mgr.terminate_session.assert_called_once_with("sess-1")


def test_create_session_preserves_launch_error_when_cleanup_also_fails():
    """G2 fix-round-1: if terminate_session raises during cleanup, the caller
    must still see the original launch failure, not the cleanup failure."""
    mgr = MagicMock()
    mgr.create_session = MagicMock(return_value="sess-compound-failure")
    mgr.terminate_session = MagicMock(side_effect=RuntimeError("teardown also broken"))

    with patch.object(expose_api, "session_manager", mgr), \
         patch.object(
             expose_api, "execute_keyword",
             AsyncMock(side_effect=RuntimeError("app not installed")),
         ), \
         patch.object(expose_api, "reconfigure_logging", MagicMock()):
        with pytest.raises(HTTPException) as exc:
            _run(expose_api.create_session(_minimal_session_config()))

    # The response must reflect the launch failure, not the cleanup failure.
    assert "app not installed" in str(exc.value.detail)
    assert "teardown also broken" not in str(exc.value.detail)
    mgr.terminate_session.assert_called_once_with("sess-compound-failure")


# ---------------------------------------------------------------------------
# _gather_workspace_data: keyword_lock serialization (Phase 0, task 4, H1)
# ---------------------------------------------------------------------------


def test_gather_workspace_data_holds_the_keyword_lock():
    """H1: the workspace poller must not issue driver commands concurrently with a keyword."""
    session = MagicMock()
    session.session_id = "sess-1"
    session.keyword_lock = asyncio.Lock()

    observed: list[bool] = []

    def _capture_screenshot_np(*_a, **_kw):
        observed.append(session.keyword_lock.locked())
        return None

    verifier = MagicMock()
    verifier._safe_capture_screenshot_np = _capture_screenshot_np
    verifier._collect_interactive_elements = MagicMock(return_value=[])
    session.optics.build.return_value = verifier

    with patch.object(expose_api, "Verifier", MagicMock()):
        _run(asyncio.wait_for(
            expose_api._gather_workspace_data(session, False, None), timeout=5
        ))

    assert observed == [True], "driver work ran without holding session.keyword_lock"


# ---------------------------------------------------------------------------
# add_session_api / event_generator guards (Phase 0, task 5, H3 & G7)
# ---------------------------------------------------------------------------


def test_add_session_api_holds_the_keyword_lock():
    """H3: swapping session.apis must not race an in-flight API keyword."""
    observed: list[bool] = []

    class _Session:
        """Real object, not a MagicMock: the apis setter must actually fire."""
        def __init__(self):
            self.session_id = "sess-1"
            self.keyword_lock = asyncio.Lock()
            self._apis = None

        @property
        def apis(self):
            return self._apis

        @apis.setter
        def apis(self, value):
            observed.append(self.keyword_lock.locked())
            self._apis = value

    session = _Session()

    with patch.object(expose_api.session_manager, "get_session", return_value=session):
        _run(asyncio.wait_for(
            expose_api.add_session_api("sess-1", {"api": {"global_defaults": {"k": "v"}}}),
            timeout=5,
        ))

    assert session.apis.global_defaults == {"k": "v"}, "the swap did not happen at all"
    assert observed == [True], "session.apis was swapped without holding keyword_lock"


def test_event_generator_stops_when_session_is_gone():
    """G7: the event stream must not outlive its session."""
    session = MagicMock()
    session.session_id = "sess-1"
    session.event_queue = asyncio.Queue()

    async def drain():
        return [chunk async for chunk in expose_api.event_generator(session)]

    with patch.object(expose_api.session_manager, "get_session", return_value=None):
        chunks = _run(asyncio.wait_for(drain(), timeout=5))

    assert chunks == [], "generator yielded after the session was terminated"


# ---------------------------------------------------------------------------
# Task 8 hardening: CORS credentials, capability logging, dead executor
# ---------------------------------------------------------------------------


def test_cors_does_not_allow_credentials_with_wildcard_origin():
    """I2: wildcard origin + credentials lets any site drive local sessions."""
    cors = [
        m for m in expose_api.app.user_middleware
        if "CORSMiddleware" in str(m.cls)
    ]
    assert cors, "CORS middleware not found"
    options = cors[0].kwargs
    if "*" in options.get("allow_origins", []):
        assert not options.get("allow_credentials", False), (
            "allow_credentials must be False when allow_origins is a wildcard"
        )


def test_create_session_does_not_log_raw_capabilities():
    """I4: capabilities carry cloud-farm access keys; never log them verbatim."""
    mgr = MagicMock()
    mgr.create_session = MagicMock(return_value="sess-1")

    config = expose_api.SessionConfig(
        driver_sources=[{
            "appium": {
                "enabled": True,
                "url": "http://127.0.0.1:4723",
                "capabilities": {"browserstack.key": "SUPERSECRET123"},
            }
        }],
        elements_sources=[], text_detection=[], image_detection=[],
    )

    logged: list[str] = []

    def _record(msg, *args, **kwargs):
        logged.append(msg % args if args else str(msg))

    with patch.object(expose_api, "session_manager", mgr), \
         patch.object(expose_api, "execute_keyword", AsyncMock(return_value=MagicMock(data={}))), \
         patch.object(expose_api, "reconfigure_logging", MagicMock()), \
         patch.object(expose_api.internal_logger, "info", _record):
        _run(asyncio.wait_for(expose_api.create_session(config), timeout=5))

    assert not any("SUPERSECRET123" in line for line in logged), (
        f"secret leaked into logs: {logged}"
    )


# ---------------------------------------------------------------------------
# Final review: teardown serialization (C1) and unknown-session contract (I5)
# ---------------------------------------------------------------------------


def _session_with_real_lock(session_id: str = "sess-mock"):
    """MagicMock session carrying a real ``asyncio.Lock`` for ``keyword_lock``."""
    session = MagicMock()
    session.session_id = session_id
    session.keyword_lock = asyncio.Lock()
    return session


def test_delete_session_waits_for_in_flight_keyword():
    """C1: teardown must not quit the driver while a keyword holds the lock.

    A keyword holds ``keyword_lock`` across ``asyncio.to_thread``, which leaves
    the loop free to serve a concurrent DELETE. Without the lock, that DELETE
    calls ``driver.quit()`` on the remote session the worker thread is still
    driving, and rmtree's the template dir a matcher may be reading.
    """
    session = _session_with_real_lock("sess-busy")
    terminated: list[str] = []
    mgr = MagicMock()
    mgr.get_session.return_value = session
    mgr.terminate_session = MagicMock(side_effect=terminated.append)

    async def scenario():
        # Stand in for a keyword request that is mid-``to_thread``.
        await session.keyword_lock.acquire()
        task = asyncio.create_task(expose_api.delete_session("sess-busy"))
        await asyncio.sleep(0.1)
        during = list(terminated)
        session.keyword_lock.release()
        await task
        return during, list(terminated)

    with patch.object(expose_api, "session_manager", mgr):
        during, after = _run(asyncio.wait_for(scenario(), timeout=5))

    assert during == [], "teardown ran while a keyword still held the session lock"
    assert after == ["sess-busy"], "teardown never ran after the lock was released"


def test_delete_session_tears_down_anyway_when_keyword_is_wedged():
    """C1: a wedged keyword must not make a session permanently un-evictable."""
    session = _session_with_real_lock("sess-wedged")
    mgr = MagicMock()
    mgr.get_session.return_value = session
    mgr.terminate_session = MagicMock()

    async def scenario():
        await session.keyword_lock.acquire()  # never released
        return await expose_api.delete_session("sess-wedged")

    with patch.object(expose_api, "session_manager", mgr), \
         patch.object(expose_api, "SESSION_TEARDOWN_LOCK_TIMEOUT_S", 0.05):
        resp = _run(asyncio.wait_for(scenario(), timeout=5))

    assert resp.status == expose_api.STATUS_TERMINATED
    mgr.terminate_session.assert_called_once_with("sess-wedged")


def test_delete_session_cancelled_while_waiting_still_evicts():
    """G1: a cancelled DELETE must not leave the session registered.

    The lock-wait is the one teardown window a cancellation can interrupt
    before ``terminate_session`` is submitted. Eviction must still happen --
    this endpoint is the only path that removes a session -- so the handler
    runs it shielded and lets the cancellation surface afterwards.
    """
    session = _session_with_real_lock("sess-cancel")
    terminated: list[str] = []
    mgr = MagicMock()
    mgr.get_session.return_value = session
    mgr.terminate_session = MagicMock(side_effect=terminated.append)

    async def scenario():
        await session.keyword_lock.acquire()  # keyword wedged; DELETE waits
        task = asyncio.create_task(expose_api.delete_session("sess-cancel"))
        await asyncio.sleep(0.05)
        assert not terminated, "teardown ran while the lock was still held"
        task.cancel()
        try:
            await task
            cancelled = False
        except asyncio.CancelledError:
            cancelled = True
        # The shielded rescue may need another loop tick to finish.
        for _ in range(100):
            if terminated:
                break
            await asyncio.sleep(0.01)
        return cancelled, list(terminated)

    with patch.object(expose_api, "session_manager", mgr):
        cancelled, after = _run(asyncio.wait_for(scenario(), timeout=5))

    assert cancelled, "cancellation was swallowed instead of surfacing"
    assert after == ["sess-cancel"], "cancelled DELETE skipped eviction"


def test_delete_session_releases_lock_when_teardown_raises():
    """C1: a failing teardown must not leave ``keyword_lock`` held."""
    session = _session_with_real_lock()
    mgr = MagicMock()
    mgr.get_session.return_value = session
    mgr.terminate_session = MagicMock(side_effect=RuntimeError("device rebooted"))

    with patch.object(expose_api, "session_manager", mgr):
        with pytest.raises(HTTPException):
            _run(asyncio.wait_for(expose_api.delete_session("sess-x"), timeout=5))

    assert not session.keyword_lock.locked()


def test_delete_unknown_session_returns_404():
    """I5: terminate_session no-ops on an unknown id; don't report success."""
    mgr = MagicMock()
    mgr.get_session.return_value = None
    mgr.terminate_session = MagicMock()

    with patch.object(expose_api, "session_manager", mgr):
        with pytest.raises(HTTPException) as exc:
            _run(asyncio.wait_for(expose_api.delete_session("nope"), timeout=5))

    assert exc.value.status_code == 404
    assert exc.value.detail == expose_api.SESSION_NOT_FOUND
    mgr.terminate_session.assert_not_called()


def test_delete_session_twice_returns_404_the_second_time(client):
    """I5: a double-delete must not claim the device was released twice."""
    session = _session_with_real_lock("sess-dd")
    sessions = {"sess-dd": session}

    with patch.object(expose_api.session_manager, "get_session", sessions.get), \
         patch.object(expose_api.session_manager, "terminate_session",
                      MagicMock(side_effect=sessions.pop)):
        first = client.delete("/v1/sessions/sess-dd/stop")
        second = client.delete("/v1/sessions/sess-dd/stop")

    assert first.status_code == 200
    assert second.status_code == 404

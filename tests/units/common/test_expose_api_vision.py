"""Tests for vision-based template resolution and execute/template APIs."""
import asyncio
import base64
import os
import tempfile
from unittest.mock import MagicMock

from optics_framework.common.models import TemplateData
import pytest
from optics_framework.common.expose_api import (
    ExecuteRequest,
    TemplateUploadRequest,
    _decode_template_base64,
    _safe_template_filename,
)
from optics_framework.common.session_manager import SessionManager
from optics_framework.common.session_manager import SessionTemplateResolver


def _make_mock_session(
    inline_templates=None,
    project_templates=None,
):
    """Build a minimal session-like object for SessionTemplateResolver."""
    class MockSession:
        pass

    s = MockSession()
    s.inline_templates = dict(inline_templates or {})
    s.templates = project_templates
    return s


def test_session_template_resolver_request_override_first():
    """Resolver returns the request-scoped ContextVar override before inline or project."""
    from optics_framework.common import session_manager as sm

    t = TemplateData()
    t.add_template("btn", "/project/btn.png")
    session = _make_mock_session(
        inline_templates={"btn": "/inline/btn.png"},
        project_templates=t,
    )
    resolver = SessionTemplateResolver(session)
    token = sm.request_template_overrides.set({"btn": "/request/btn.png"})
    try:
        assert resolver.get_template_path("btn") == "/request/btn.png"
    finally:
        sm.request_template_overrides.reset(token)


def test_session_template_resolver_inline_then_project():
    """Resolver returns inline_templates when no request override."""
    t = TemplateData()
    t.add_template("btn", "/project/btn.png")
    session = _make_mock_session(
        inline_templates={"btn": "/inline/btn.png"},
        project_templates=t,
    )
    resolver = SessionTemplateResolver(session)
    assert resolver.get_template_path("btn") == "/inline/btn.png"
    session.inline_templates.clear()
    assert resolver.get_template_path("btn") == "/project/btn.png"


def test_session_template_resolver_project_only():
    """Resolver returns project templates when no overrides."""
    t = TemplateData()
    t.add_template("x", "/proj/x.png")
    session = _make_mock_session(project_templates=t)
    resolver = SessionTemplateResolver(session)
    assert resolver.get_template_path("x") == "/proj/x.png"


def test_session_template_resolver_none_when_missing():
    """Resolver returns None when name is not in any source."""
    session = _make_mock_session(project_templates=TemplateData())
    resolver = SessionTemplateResolver(session)
    assert resolver.get_template_path("nonexistent") is None


def test_session_template_resolver_none_when_no_templates():
    """Resolver returns None when session.templates is None and no overrides."""
    session = _make_mock_session()
    resolver = SessionTemplateResolver(session)
    assert resolver.get_template_path("x") is None


def test_decode_template_base64_raw():
    """_decode_template_base64 accepts raw base64."""

    raw = b"\x89PNG\r\n\x1a\n"
    b64 = base64.b64encode(raw).decode("ascii")
    assert _decode_template_base64(b64) == raw


def test_decode_template_base64_data_url():
    """_decode_template_base64 accepts data URL."""

    raw = b"\x89PNG\r\n\x1a\n"
    b64 = base64.b64encode(raw).decode("ascii")
    data_url = f"data:image/png;base64,{b64}"
    assert _decode_template_base64(data_url) == raw


def test_execute_request_accepts_template_images():
    """ExecuteRequest accepts optional template_images."""

    r = ExecuteRequest(
        mode="keyword",
        keyword="Press Element",
        params={"element": "my_btn"},
        template_images={"my_btn": "iVBORw0KGgo="},
    )
    assert r.template_images == {"my_btn": "iVBORw0KGgo="}
    r2 = ExecuteRequest(mode="keyword", keyword="Press Element", params=[])
    assert r2.template_images is None


def test_upload_template_request_model():
    """TemplateUploadRequest accepts name and image_base64."""

    body = TemplateUploadRequest(name="btn1", image_base64="abc123")
    assert body.name == "btn1"
    assert body.image_base64 == "abc123"


def test_safe_template_filename_name_png_for_safe_names():
    """_safe_template_filename yields name.png-style stems for safe names; rejects path-like ones."""

    assert _safe_template_filename("my_btn") == "my_btn"
    assert _safe_template_filename("btn1") == "btn1"
    assert _safe_template_filename("x-y.z") == "x-y.z"
    assert _safe_template_filename("my btn") == "my_btn"
    # Path-like or reserved -> reject (ValueError)
    with pytest.raises(ValueError, match="path segments"):
        _safe_template_filename("../../../etc/passwd")
    with pytest.raises(ValueError, match="path segments"):
        _safe_template_filename("a/b")
    with pytest.raises(ValueError, match="path segments"):
        _safe_template_filename("..")


def test_terminate_cleans_inline_templates_dir():
    """terminate_session removes the session's inline-templates dir (server-created, not from user input)."""

    session_id = "test-session-terminate-cleanup"
    # Session's _inline_templates_dir is created by the server (mkdtemp); simulate it for this test
    session_dir = tempfile.mkdtemp(prefix="optics_session_")
    marker = os.path.join(session_dir, "uploaded.png")
    with open(marker, "wb") as f:
        f.write(b"x")
    assert os.path.isdir(session_dir)

    manager = SessionManager()
    session = type("Session", (), {"driver": None, "inline_templates": {}, "_inline_templates_dir": session_dir})()
    manager.sessions[session_id] = session
    manager.terminate_session(session_id)
    assert not os.path.isdir(session_dir)


def test_request_template_overrides_are_isolated_per_task():
    """H2: one request's overrides must be invisible to a concurrent request."""
    from optics_framework.common import session_manager as sm

    session = MagicMock()
    session.inline_templates = {}
    session.templates = None
    resolver = sm.SessionTemplateResolver(session)

    seen: dict = {}

    async def one_request(name: str, path: str, settle: asyncio.Event, go: asyncio.Event):
        token = sm.request_template_overrides.set({name: path})
        try:
            settle.set()
            await go.wait()
            # After the sibling request has set *its* overrides, ours must survive.
            seen[name] = resolver.get_template_path(name)
        finally:
            sm.request_template_overrides.reset(token)

    async def scenario():
        a_ready, b_ready, go = asyncio.Event(), asyncio.Event(), asyncio.Event()
        task_a = asyncio.create_task(one_request("login_btn", "/tmp/a.png", a_ready, go))
        task_b = asyncio.create_task(one_request("cancel_btn", "/tmp/b.png", b_ready, go))
        await a_ready.wait()
        await b_ready.wait()
        go.set()
        await asyncio.gather(task_a, task_b)

    # Bound the whole scenario, not just the final gather: if
    # request_template_overrides.set(...) ever regresses to something that
    # raises before settle.set() (e.g. reverted to a plain dict/session
    # field), a_ready/b_ready never fire and scenario() hangs forever on
    # `await a_ready.wait()`. wait_for around scenario() turns that hang into
    # a fast, legible TimeoutError instead of burning the whole CI job.
    asyncio.run(asyncio.wait_for(scenario(), timeout=5))

    assert seen == {"login_btn": "/tmp/a.png", "cancel_btn": "/tmp/b.png"}

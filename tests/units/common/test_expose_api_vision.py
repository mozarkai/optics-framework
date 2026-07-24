"""Tests for vision-based template resolution and execute/template APIs."""
import base64
import os
import tempfile

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
    request_overrides=None,
    inline_templates=None,
    project_templates=None,
):
    """Build a minimal session-like object for SessionTemplateResolver."""
    class MockSession:
        pass

    s = MockSession()
    s.request_template_overrides = dict(request_overrides or {})
    s.inline_templates = dict(inline_templates or {})
    s.templates = project_templates
    return s


def test_session_template_resolver_request_override_first():
    """Resolver returns request_template_overrides before inline or project."""
    t = TemplateData()
    t.add_template("btn", "/project/btn.png")
    session = _make_mock_session(
        request_overrides={"btn": "/request/btn.png"},
        inline_templates={"btn": "/inline/btn.png"},
        project_templates=t,
    )
    resolver = SessionTemplateResolver(session)
    assert resolver.get_template_path("btn") == "/request/btn.png"


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

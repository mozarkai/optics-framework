"""Tests for vision-based template resolution and execute/template APIs."""
import base64

from optics_framework.common.models import TemplateData
import cv2
import numpy as np
from optics_framework.common.expose_api import (
    _decode_template_base64,
)
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


def test_session_template_resolver_none_when_missing():
    """Resolver returns None when name is not in any source."""
    session = _make_mock_session(project_templates=TemplateData())
    resolver = SessionTemplateResolver(session)
    assert resolver.get_template_path("nonexistent") is None


_TINY_PNG = cv2.imencode(".png", np.zeros((2, 2, 3), np.uint8))[1].tobytes()


def test_decode_template_base64_data_url():
    """_decode_template_base64 accepts data URL."""

    raw = _TINY_PNG
    b64 = base64.b64encode(raw).decode("ascii")
    data_url = f"data:image/png;base64,{b64}"
    assert _decode_template_base64(data_url) == raw

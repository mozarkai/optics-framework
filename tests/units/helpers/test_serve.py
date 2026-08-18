"""Unit tests for ``optics_framework/helper/serve.py``.

Covers the ``--workers`` guard: each worker process gets its own in-memory
``SessionManager`` (see ``optics_framework/common/expose_api.py``), so running
more than one worker silently corrupts session lookups. ``run_uvicorn_server``
must refuse rather than passing ``workers`` straight through to uvicorn.
"""
from __future__ import annotations

import pytest

from optics_framework.common.error import Code, OpticsError
from optics_framework.helper import serve


def test_multiple_workers_is_refused():
    """G8: each worker has its own in-memory SessionManager, so >1 worker breaks lookups."""
    with pytest.raises(OpticsError) as exc:
        serve.run_uvicorn_server(host="127.0.0.1", port=8000, workers=4)
    assert exc.value.code == Code.E0501
    assert "workers" in str(exc.value).lower()


def test_single_worker_is_allowed(monkeypatch):
    called = {}
    monkeypatch.setattr(serve.uvicorn, "run", lambda *a, **kw: called.update(kw))
    serve.run_uvicorn_server(host="127.0.0.1", port=8000, workers=1)
    assert called["workers"] == 1

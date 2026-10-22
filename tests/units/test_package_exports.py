"""Contract tests for what ``optics_framework`` itself exports.

The package resolves the documented ``Optics`` facade lazily (:pep:`562`) so a
submodule import doesn't drag in the vision stack. These pin the export surface
that laziness must not change. They live outside ``test_optics.py`` because that
module is skipped without easyocr, and this contract has no engine dependency.
"""
from __future__ import annotations

import pytest

import optics_framework
from optics_framework.optics import Optics

pytestmark = pytest.mark.white_box


def test_optics_is_reexported_from_package_root():
    """The documented ``from optics_framework import Optics`` import must resolve."""
    from optics_framework import Optics as RootOptics

    assert RootOptics is Optics
    assert "Optics" in optics_framework.__all__
    assert "Optics" in dir(optics_framework)


def test_star_import_binds_optics():
    """``from optics_framework import *`` bound Optics before the lazy re-export
    and must keep doing so: an empty ``__all__`` would silently export nothing."""
    namespace: dict = {}
    exec("from optics_framework import *", namespace)  # nosec B102

    assert namespace["Optics"] is Optics


def test_unknown_attribute_still_raises_attribute_error():
    with pytest.raises(AttributeError):
        optics_framework.does_not_exist  # noqa: B018

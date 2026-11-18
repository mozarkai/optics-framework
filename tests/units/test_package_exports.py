"""Contract tests for what ``optics_framework`` itself exports."""
from __future__ import annotations


import optics_framework
from optics_framework.optics import Optics


def test_optics_is_reexported_from_package_root():
    from optics_framework import Optics as RootOptics

    assert RootOptics is Optics
    assert "Optics" in optics_framework.__all__
    assert "Optics" in dir(optics_framework)

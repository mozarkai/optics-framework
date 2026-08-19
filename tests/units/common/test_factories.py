"""Unit tests for factory dynamic loading and engine aliases.

Covers TextFactory resolving both 'googlevision' (legacy / canonical module name)
and 'google_vision' (config key alias / compatibility shim).
"""
import pytest

from optics_framework.common.config_handler import DependencyConfig
from optics_framework.common.factories import TextFactory
from optics_framework.engines.vision_models.ocr_models.googlevision import (
    GoogleVisionHelper as CanonicalGoogleVisionHelper,
)
from optics_framework.engines.vision_models.ocr_models.google_vision import (
    GoogleVisionHelper as ShimGoogleVisionHelper,
)


@pytest.mark.white_box
class TestTextFactoryGoogleVisionResolution:
    def test_direct_imports_match(self):
        assert CanonicalGoogleVisionHelper is ShimGoogleVisionHelper

    def test_text_factory_resolves_googlevision(self):
        TextFactory.clear_instances()
        fallback = TextFactory.get_driver([{"googlevision": DependencyConfig(enabled=True)}])
        assert fallback is not None
        assert isinstance(fallback.active_instance, CanonicalGoogleVisionHelper)

    def test_text_factory_resolves_google_vision_alias(self):
        TextFactory.clear_instances()
        fallback = TextFactory.get_driver([{"google_vision": DependencyConfig(enabled=True)}])
        assert fallback is not None
        assert isinstance(fallback.active_instance, CanonicalGoogleVisionHelper)

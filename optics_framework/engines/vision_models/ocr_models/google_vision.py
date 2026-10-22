"""Compatibility shim for google_vision -> googlevision.

Allows both 'google_vision' and 'googlevision' to be used in configuration and imports.
"""
from optics_framework.engines.vision_models.ocr_models.googlevision import (
    GoogleVisionHelper,
)

__all__ = ["GoogleVisionHelper"]

import importlib.util
import sys

# Engines that read a frame on this machine, fastest first, with the modules each one imports.
LOCAL_ENGINES = (
    ("apple_vision", ("Vision",)),
    ("rapidocr", ("rapidocr", "onnxruntime")),
    ("easyocr", ("easyocr",)),
    ("pytesseract", ("pytesseract",)),
)


def installed_local_engines() -> list[str]:
    return [name for name, modules in LOCAL_ENGINES if all(importlib.util.find_spec(m) for m in modules)]


def preferred_local_engine() -> str:
    """The fastest installed engine, else the fastest one this OS can install."""
    installed = installed_local_engines()
    if installed:
        return installed[0]
    return "apple_vision" if sys.platform == "darwin" else "rapidocr"

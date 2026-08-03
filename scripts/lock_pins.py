#!/usr/bin/env python3
import shlex

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11 (e.g. Ubuntu 22.04's apt python3.10)
    import tomli as tomllib

with open("poetry.lock", "rb") as f:
    pkgs = {p["name"]: p["version"] for p in tomllib.load(f)["package"]}

# name -> poetry.lock package
PINS = {
    "APPIUM_VERSION": "appium-python-client",
    "PLAYWRIGHT_VERSION": "playwright",
    "EASYOCR_VERSION": "easyocr",
    "GOOGLE_CLOUD_VISION_VERSION": "google-cloud-vision",
    "PYTESSERACT_VERSION": "pytesseract",
    "MKDOCS_MATERIAL_VERSION": "mkdocs-material",
    "MKDOCSTRINGS_VERSION": "mkdocstrings",
    "MKDOCSTRINGS_PYTHON_VERSION": "mkdocstrings-python",
    "MKDOCS_MINIFY_PLUGIN_VERSION": "mkdocs-minify-plugin",
    "UVICORN_VERSION": "uvicorn",
}

for var, pkg in PINS.items():
    print(f"{var}={shlex.quote(pkgs[pkg])}")

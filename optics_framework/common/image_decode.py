"""Decode images only after their header shows they fit the memory budget."""

import os
from collections.abc import Callable
from typing import Literal
import cv2
import numpy as np
from optics_framework.common.error import Code, OpticsError

MAX_ENCODED_BYTES = 64 * 1024 * 1024
# An 8K screen (33 Mpx) and a 1920x34000 full-page capture fit; a 20000x20000 bomb (400 Mpx) does not.
MAX_PIXELS = 64 * 1024 * 1024

_JPEG_SOF = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})
_JPEG_STANDALONE = frozenset({0x01, *range(0xD0, 0xD8)})
_TIFF_SHORT, _TIFF_LONG = 3, 4
_TIFF_WIDTH, _TIFF_HEIGHT = 256, 257
_ByteOrder = Literal["little", "big"]


def _refused(reason: str) -> OpticsError:
    return OpticsError(Code.E0304, message=f"Image refused: {reason}")


def _uint(data: bytes, start: int, size: int, order: _ByteOrder = "little", signed: bool = False) -> int:
    chunk = data[start:start + size]
    if len(chunk) != size:
        raise _refused("the header is truncated")
    return int.from_bytes(chunk, order, signed=signed)


def _png_size(data: bytes) -> tuple[int, int]:
    if data[12:16] != b"IHDR":
        raise _refused("the PNG has no IHDR header")
    return _uint(data, 16, 4, "big"), _uint(data, 20, 4, "big")


def _jpeg_size(data: bytes) -> tuple[int, int]:
    pos = 2
    while True:
        if _uint(data, pos, 1) != 0xFF:
            raise _refused("the JPEG markers are malformed")
        while _uint(data, pos, 1) == 0xFF:
            pos += 1
        marker = _uint(data, pos, 1)
        pos += 1
        if marker in _JPEG_STANDALONE:
            continue
        if marker in _JPEG_SOF:
            return _uint(data, pos + 5, 2, "big"), _uint(data, pos + 3, 2, "big")
        if marker in (0xD9, 0xDA):
            raise _refused("the JPEG has no frame header")
        pos += _uint(data, pos, 2, "big")


def _gif_size(data: bytes) -> tuple[int, int]:
    return _uint(data, 6, 2), _uint(data, 8, 2)


def _bmp_size(data: bytes) -> tuple[int, int]:
    if _uint(data, 14, 4) == 12:
        return _uint(data, 18, 2), _uint(data, 20, 2)
    return _uint(data, 18, 4, signed=True), abs(_uint(data, 22, 4, signed=True))


def _webp_size(data: bytes) -> tuple[int, int]:
    chunk = data[12:16]
    if chunk == b"VP8X":
        return 1 + _uint(data, 24, 3), 1 + _uint(data, 27, 3)
    if chunk == b"VP8L" and _uint(data, 20, 1) == 0x2F:
        bits = _uint(data, 21, 4)
        return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
    if chunk == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
        return _uint(data, 26, 2) & 0x3FFF, _uint(data, 28, 2) & 0x3FFF
    raise _refused("the WebP header is malformed")


def _tiff_size(data: bytes) -> tuple[int, int]:
    order: _ByteOrder = "little" if data[:2] == b"II" else "big"
    ifd = _uint(data, 4, 4, order)
    dims = {_TIFF_WIDTH: 0, _TIFF_HEIGHT: 0}
    for entry in range(ifd + 2, ifd + 2 + 12 * _uint(data, ifd, 2, order), 12):
        tag = _uint(data, entry, 2, order)
        if tag not in dims:
            continue
        kind = _uint(data, entry + 2, 2, order)
        if kind not in (_TIFF_SHORT, _TIFF_LONG):
            raise _refused("the TIFF size tags are malformed")
        dims[tag] = _uint(data, entry + 8, 2 if kind == _TIFF_SHORT else 4, order)
    return dims[_TIFF_WIDTH], dims[_TIFF_HEIGHT]


def _size_reader(data: bytes) -> Callable[[bytes], tuple[int, int]]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return _png_size
    if data.startswith(b"\xff\xd8"):
        return _jpeg_size
    if data.startswith((b"GIF87a", b"GIF89a")):
        return _gif_size
    if data.startswith(b"BM"):
        return _bmp_size
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return _webp_size
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return _tiff_size
    raise _refused("not a PNG, JPEG, GIF, BMP, WebP or TIFF image")


def check_encoded_length(length: int) -> None:
    """Raise E0304 when an encoded image of ``length`` bytes is over budget."""
    if length > MAX_ENCODED_BYTES:
        raise _refused(f"{length} bytes is over the {MAX_ENCODED_BYTES}-byte limit")


def image_size(data: bytes) -> tuple[int, int]:
    """Width and height from the header, without decoding; raises E0304 when unknown, malformed or over budget."""
    check_encoded_length(len(data))
    width, height = _size_reader(data)(data)
    if width <= 0 or height <= 0:
        raise _refused(f"the header declares a {width}x{height} image")
    if width * height > MAX_PIXELS:
        raise _refused(f"{width}x{height} is over the {MAX_PIXELS}-pixel limit")
    return width, height


def decode_image(data: bytes, flags: int = cv2.IMREAD_COLOR) -> np.ndarray:
    """Decode encoded image bytes with OpenCV once the header is within budget; raises E0304 otherwise."""
    image_size(data)
    image = cv2.imdecode(np.frombuffer(data, np.uint8), flags)
    if image is None:
        raise _refused("OpenCV could not decode it")
    return image


def read_image(path: str, flags: int = cv2.IMREAD_COLOR) -> np.ndarray:
    """Read and decode an image file through :func:`decode_image`."""
    check_encoded_length(os.path.getsize(path))
    with open(path, "rb") as file:
        return decode_image(file.read(), flags)

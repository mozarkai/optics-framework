"""Helpers for the REST dry-run endpoints."""
from __future__ import annotations

import io
import os
import re
import zipfile
from typing import Iterable, Tuple


MAX_INLINE_BODY_BYTES = 5 * 1024 * 1024        # 5 MiB JSON body
MAX_UPLOAD_BYTES = 10 * 1024 * 1024            # 10 MiB total received upload
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024      # 50 MiB total after decompression
MAX_ARCHIVE_ENTRIES = 2000                     # number of members in a zip
MAX_COMPRESSION_RATIO = 200                    # per-entry expansion ceiling
_CHUNK = 64 * 1024

SUITE_FILE_EXTENSIONS = {".csv", ".yaml", ".yml"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif"}
_ALLOWED_EXTENSIONS = SUITE_FILE_EXTENSIONS | IMAGE_EXTENSIONS


class PayloadTooLarge(Exception):
    """Raised when an inline body or upload exceeds a limit."""


class UnsafeArchive(Exception):
    """Raised for malformed or unsafe archives."""


def safe_suite_filename(name: str) -> str:
    """Return a sanitized basename, preserving a recognized extension."""
    base = os.path.basename(name or "")
    if not base or base in (".", "..") or "/" in name or "\\" in name:
        raise UnsafeArchive(f"unsafe filename: {name!r}")
    stem, ext = os.path.splitext(base)
    stem = re.sub(r"[^a-zA-Z0-9_.-]", "_", stem).strip("._")
    ext = ext.lower()
    if not stem:
        raise UnsafeArchive(f"unsafe filename: {name!r}")
    if len(stem) > 200:
        raise UnsafeArchive(f"filename too long: {name!r}")
    return f"{stem}{ext}"


def is_suite_relevant(filename: str) -> bool:
    """True for the file extensions a suite folder may contain."""
    return os.path.splitext(filename)[1].lower() in _ALLOWED_EXTENSIONS


def _resolve_within(base_real: str, dest_dir: str, member_name: str) -> str:
    """Resolve a member path under ``dest_dir`` safely."""
    normalized = os.path.normpath(member_name)
    if os.path.isabs(normalized) or normalized.startswith(".."):
        raise UnsafeArchive(f"unsafe path in archive: {member_name!r}")
    target = os.path.realpath(os.path.join(dest_dir, normalized))
    if os.path.commonpath([base_real, target]) != base_real:
        raise UnsafeArchive(f"path escapes archive root: {member_name!r}")
    return target


def write_uploaded_files(files: Iterable[Tuple[str, bytes]], dest_dir: str) -> int:
    """Write already-read upload files into ``dest_dir``."""
    base_real = os.path.realpath(dest_dir)
    total = 0
    written = 0
    for filename, data in files:
        total += len(data)
        if total > MAX_UPLOAD_BYTES:
            raise PayloadTooLarge("upload exceeds maximum size")
        if not is_suite_relevant(filename):
            continue
        safe = safe_suite_filename(filename)
        target = _resolve_within(base_real, dest_dir, safe)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(data)
        written += 1
    return written


def _extract_zip_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, target: str, current_total: int) -> int:
    """Extract a single zip member to target, returning the bytes written."""
    os.makedirs(os.path.dirname(target), exist_ok=True)
    entry_written = 0
    with archive.open(info, "r") as src, open(target, "wb") as dst:
        while True:
            chunk = src.read(_CHUNK)
            if not chunk:
                break
            entry_written += len(chunk)
            current_total += len(chunk)
            if current_total > MAX_UNCOMPRESSED_BYTES:
                raise PayloadTooLarge("archive expands beyond maximum size")
            dst.write(chunk)
    
    if info.compress_size > 0 and entry_written / info.compress_size > MAX_COMPRESSION_RATIO:
        raise PayloadTooLarge("archive entry has a suspicious compression ratio")
    return entry_written


def safe_extract_zip(data: bytes, dest_dir: str) -> int:
    """Extract suite files from an in-memory zip into ``dest_dir`` safely."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise PayloadTooLarge("upload exceeds maximum size")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UnsafeArchive("not a valid zip archive") from exc

    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise UnsafeArchive("archive has too many entries")

    base_real = os.path.realpath(dest_dir)
    total_written = 0
    written = 0
    for info in infos:
        if info.is_dir():
            continue
        # Validate path even for skipped files
        target = _resolve_within(base_real, dest_dir, info.filename)
        if not is_suite_relevant(info.filename):
            continue
        
        entry_written = _extract_zip_member(archive, info, target, total_written)
        total_written += entry_written
        written += 1
    return written

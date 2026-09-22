"""Unit tests for console output on a stream that cannot encode every character.

Reproduces the Windows CI failure without a Windows runner: ``cp1252`` is what
Python picks for a redirected ``sys.stdout`` there, and it encodes none of the
emoji, ticks or arrows the framework prints. The bug is not Windows-specific in
mechanism — ``LC_ALL=C`` on Linux gives an ``ascii`` stdout with the same result
— so every test here runs on every platform.
"""
from __future__ import annotations

import importlib
import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console

import optics_framework
from optics_framework.helper import doctor
from optics_framework.helper.console_encoding import ensure_console_encoding

pytestmark = pytest.mark.white_box

DOCTOR_BANNER = "[bold]\U0001fa7a optics doctor[/bold]\n"


def _cp1252_stream() -> io.TextIOWrapper:
    """A stdout stand-in with Windows' default redirected-output encoding."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")


def _unencodable_characters_in_package() -> set[str]:
    """Every character in the shipped source that ``cp1252`` cannot represent."""
    root = Path(optics_framework.__file__).resolve().parent
    found: set[str] = set()
    for path in root.rglob("*.py"):
        for char in path.read_text(encoding="utf-8"):
            if char.isascii():
                continue
            try:
                char.encode("cp1252")
            except UnicodeEncodeError:
                found.add(char)
    return found


class TestEnsureConsoleEncoding:
    def test_rich_print_no_longer_raises_on_an_unencodable_stream(self):
        """The reported failure: the doctor banner aborted the command."""
        stream = _cp1252_stream()
        with patch.object(sys, "stdout", stream):
            ensure_console_encoding()
            Console(file=sys.stdout, force_terminal=False).print(DOCTOR_BANNER)
            sys.stdout.flush()

        assert stream.buffer.getvalue() == b"? optics doctor\n\n"

    def test_module_level_consoles_are_covered_without_being_rebuilt(self):
        """``doctor._console`` is built at import time and resolves ``sys.stdout``
        at write time, so relaxing the stream reaches it retroactively."""
        stream = _cp1252_stream()
        with patch.object(sys, "stdout", stream):
            ensure_console_encoding()
            doctor._console.print(DOCTOR_BANNER)
            for glyph in doctor._STATUS_GLYPH.values():
                doctor._console.print(glyph)
            sys.stdout.flush()

        assert b"optics doctor" in stream.buffer.getvalue()

    def test_every_unencodable_character_the_package_prints_survives(self):
        """One choke point has to cover the whole family, not just doctor."""
        characters = _unencodable_characters_in_package()
        assert characters, "scan found nothing; the probe is no longer meaningful"

        stream = _cp1252_stream()
        with patch.object(sys, "stdout", stream):
            ensure_console_encoding()
            Console(file=sys.stdout, force_terminal=False).print(
                "".join(sorted(characters)), markup=False)
            sys.stdout.flush()

        assert stream.buffer.getvalue()

    def test_a_utf8_stream_is_left_untouched(self):
        """A stream that can already encode our output must not be reconfigured:
        a working pipeline keeps receiving exactly the bytes it did before."""
        stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
        with patch.object(sys, "stdout", stream):
            ensure_console_encoding()

        assert stream.errors == "strict"

    def test_a_non_raising_error_handler_is_left_untouched(self):
        """``PYTHONIOENCODING=cp1252:backslashreplace`` already degrades without
        crashing; overriding it would discard the user's explicit choice."""
        stream = io.TextIOWrapper(
            io.BytesIO(), encoding="cp1252", errors="backslashreplace")
        with patch.object(sys, "stdout", stream):
            ensure_console_encoding()

        assert stream.errors == "backslashreplace"

    def test_a_stream_that_cannot_be_reconfigured_is_ignored(self):
        """Embedding hosts and test harnesses substitute streams that are not
        ``TextIOWrapper``; they must not turn into an import-time crash."""
        with patch.object(sys, "stdout", io.StringIO()):
            ensure_console_encoding()

    def test_reconfigure_failure_is_swallowed(self):
        stream = _cp1252_stream()
        with patch.object(stream, "reconfigure", side_effect=ValueError("detached")):
            with patch.object(sys, "stdout", stream):
                ensure_console_encoding()


class TestPackageImportWiring:
    def test_importing_the_package_relaxes_an_unencodable_stream(self):
        """Every entry point reaches the package root, so the fix lives there."""
        stream = _cp1252_stream()
        with patch.object(sys, "stdout", stream):
            importlib.reload(optics_framework)
            assert sys.stdout.errors == "replace"

    def test_reload_leaves_the_lazy_facade_intact(self):
        """The eager import added to ``__init__`` must not break PEP 562."""
        importlib.reload(optics_framework)

        assert optics_framework.__all__ == ["Optics"]

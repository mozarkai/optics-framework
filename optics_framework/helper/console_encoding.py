"""Keep console output printable when the stream cannot encode every character.

Optics prints emoji, ticks and arrows from roughly a dozen modules — the doctor
banner and its per-row status glyphs, the onboarding wave, the error panel, the
runner's tree printer. ``sys.stdout`` is only UTF-8 when it is attached to a real
console: under a pipe, a redirect or CI, Python falls back to the locale encoding
(``cp1252`` on Windows, ``ascii`` under ``LC_ALL=C``), and the first unencodable
character aborts the command with a ``UnicodeEncodeError`` before a single row is
printed. rich's ``safe_box`` downgrades the box drawing but does not transcode
text, so no amount of per-call-site care fixes this.

Relaxing the stream's error handler fixes every one of those call sites at once,
because rich resolves ``sys.stdout`` at write time and ``reconfigure`` mutates the
stream in place. The encoding itself is left alone: a pipeline that already
decodes our output keeps receiving the same bytes for everything it could encode
before, and only characters it never had a representation for become ``?``.

Stdlib-only by requirement — the package root imports it while the rest of the
package may still be failing to load.
"""

import sys
from typing import Any

# The widest characters the framework prints: the doctor banner emoji, a status
# tick and an arrow. Any codec that encodes all three encodes everything else we
# emit; cp1252 encodes none of them.
_PROBE = "\U0001fa7a✅→"

# Error handlers that raise on an unencodable character. Every other handler
# already substitutes something, so a stream using one degrades on its own.
_RAISING_HANDLERS = frozenset({"strict", "surrogateescape", "surrogatepass"})


def _can_encode(encoding: str) -> bool:
    try:
        _PROBE.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def _relax_stream(stream: Any) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    encoding = getattr(stream, "encoding", None)
    if reconfigure is None or not encoding:
        return
    if getattr(stream, "errors", None) not in _RAISING_HANDLERS:
        return
    if _can_encode(encoding):
        return
    try:
        reconfigure(errors="replace")
    except (ValueError, OSError):
        pass


def ensure_console_encoding() -> None:
    """Make ``sys.stdout``/``sys.stderr`` substitute unencodable characters.

    A no-op on a stream that can already encode what we print, that has a
    non-raising error handler, or that cannot be reconfigured at all. Safe to
    call repeatedly.
    """
    _relax_stream(sys.stdout)
    _relax_stream(sys.stderr)

import asyncio
import io
import sys

import pytest

from optics_framework.common.events import Event, EventStatus
from optics_framework.common.runner.event_stream import EventStreamPrinter, OutputMode, output_mode_for


class _Stream(io.StringIO):
    def __init__(self, tty=False, encoding="utf-8"):
        super().__init__()
        self._tty = tty
        self._encoding = encoding

    @property
    def encoding(self):
        return self._encoding

    def isatty(self):
        return self._tty

    def write(self, text):
        text.encode(self._encoding)
        return super().write(text)


@pytest.mark.parametrize(("output", "use_printer", "tty", "expected"), [
    (None, True, True, OutputMode.TREE),
    (None, True, False, OutputMode.PLAIN),
    (None, False, True, OutputMode.NONE),
    (OutputMode.JSONL, False, True, OutputMode.JSONL),
])
def test_the_mode_follows_the_terminal_unless_chosen(monkeypatch, output, use_printer, tty, expected):
    monkeypatch.setattr(sys, "stdout", _Stream(tty=tty))
    assert output_mode_for(output, use_printer) is expected


def test_a_name_the_stream_cannot_encode_gets_a_replacement():
    stream = _Stream(encoding="cp1252")
    printer = EventStreamPrinter(OutputMode.PLAIN, stream)
    asyncio.run(printer.on_event(Event(entity_type="keyword", entity_id="k", name="Tap ✓", status=EventStatus.PASS)))
    assert 'keyword "Tap ?"' in stream.getvalue()

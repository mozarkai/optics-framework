"""Unit tests for the post-summary console-log gate.

Source under test: ``logging_config.quiet_console_logs`` / ``restore_console_logs``
and their wiring into ``TreeResultPrinter.stop_live`` / ``start_live``. Once the
result printer has rendered its final summary panel, teardown INFO records
(``[Playwright] Terminating session``, ``[AsyncUtils] Creating persistent event
loop``) must stop reaching the *console* handler — while file handlers keep their
configured levels and still record everything.
"""
import logging
from unittest.mock import MagicMock

import pytest

from optics_framework.common.logging_config import (
    internal_logger,
    logging_manager,
    quiet_console_logs,
    restore_console_logs,
)
from optics_framework.common.runner import printers as printers_module
from optics_framework.common.runner.printers import (
    TerminalWidthProvider,
    TreeResultPrinter,
)

pytestmark = pytest.mark.white_box


@pytest.fixture(autouse=True)
def _leave_console_loud():
    yield
    restore_console_logs()
    TreeResultPrinter.get_instance(TerminalWidthProvider())._live = None


class TestQuietConsoleLogs:
    def test_console_handler_drops_to_warning(self):
        console = logging_manager.internal_console_handler
        restore_console_logs()
        assert console.level != logging.WARNING or internal_logger.level == logging.WARNING
        quiet_console_logs()
        assert console.level == logging.WARNING


class TestPrinterGateWiring:
    def _printer(self) -> TreeResultPrinter:
        return TreeResultPrinter.get_instance(TerminalWidthProvider())

    def test_stop_live_quiets_console(self):
        printer = self._printer()
        printer._live = MagicMock()
        restore_console_logs()

        printer.stop_live()

        assert printer._live is None
        assert logging_manager.internal_console_handler.level == logging.WARNING

    def test_start_live_restores_console(self, monkeypatch):
        printer = self._printer()
        printer._live = None
        monkeypatch.setattr(printers_module, "Live", MagicMock())
        monkeypatch.setattr(
            printers_module, "get_console", lambda: type("C", (), {"force_terminal": False})()
        )
        quiet_console_logs()

        printer.start_live()

        assert logging_manager.internal_console_handler.level == internal_logger.level
        assert logging_manager.internal_console_handler.level != logging.WARNING or (
            internal_logger.level == logging.WARNING
        )

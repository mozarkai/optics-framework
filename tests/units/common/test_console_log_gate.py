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

    def test_file_handlers_keep_their_levels(self, tmp_path):
        file_handler = logging.FileHandler(tmp_path / "internal_logs.log")
        file_handler.setLevel(logging.DEBUG)
        internal_logger.addHandler(file_handler)
        try:
            quiet_console_logs()
            assert file_handler.level == logging.DEBUG
            assert logging_manager.internal_console_handler.level == logging.WARNING
        finally:
            internal_logger.removeHandler(file_handler)
            file_handler.close()

    def test_info_records_still_reach_file_after_quiet(self, tmp_path):
        log_file = tmp_path / "internal_logs.log"
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        previous_level = internal_logger.level
        internal_logger.setLevel(logging.INFO)  # mirrors initialize_handlers
        internal_logger.addHandler(file_handler)
        try:
            logging_manager.internal_console_handler.setLevel(logging.INFO)
            quiet_console_logs()
            internal_logger.info("[Playwright] Terminating session")
        finally:
            internal_logger.removeHandler(file_handler)
            internal_logger.setLevel(previous_level)
            file_handler.close()
        assert "[Playwright] Terminating session" in log_file.read_text()

    def test_restore_returns_console_to_logger_level(self):
        quiet_console_logs()
        assert logging_manager.internal_console_handler.level == logging.WARNING
        restore_console_logs()
        assert logging_manager.internal_console_handler.level == internal_logger.level


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

    def test_stop_live_without_display_is_a_no_op(self):
        printer = self._printer()
        printer._live = None
        restore_console_logs()

        printer.stop_live()

        assert logging_manager.internal_console_handler.level == internal_logger.level

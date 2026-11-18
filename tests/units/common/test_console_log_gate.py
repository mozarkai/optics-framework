import logging

import pytest

from optics_framework.common.logging_config import (
    internal_logger,
    logging_manager,
    quiet_console_logs,
    restore_console_logs,
)
from optics_framework.common.runner.printers import (
    TerminalWidthProvider,
    TreeResultPrinter,
)


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

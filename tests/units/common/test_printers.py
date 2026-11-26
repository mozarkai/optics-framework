"""TreeResultPrinter's Failures panel, rendered to a recorded console."""
import io

from rich.console import Console

from optics_framework.common.runner import printers
from optics_framework.common.runner.printers import (
    KeywordResult,
    ModuleResult,
    TerminalWidthProvider,
    TreeResultPrinter,
)
# Aliased so pytest does not collect it.
from optics_framework.common.runner.printers import TestCaseResult as _TestCaseResult


class TestFailuresPanel:
    @staticmethod
    def _rendered(monkeypatch, test_state):
        console = Console(record=True, width=200, file=io.StringIO())
        monkeypatch.setattr(printers, "get_console", lambda: console)
        printer = TreeResultPrinter(TerminalWidthProvider())
        printer.start_live()
        for result in test_state.values():
            printer.print_tree_log(result)
        printer.stop_live()
        return console.export_text()

    def test_shows_where_each_step_failed_with_its_reason_and_hint(self, monkeypatch):
        keyword = KeywordResult(
            id="k1", name="Press Element", resolved_name="Press Element (btn)", elapsed="0.10s", status="FAIL",
            reason="login_button is not on the screen.", hint="Check that the app shows the screen you expect.",
        )
        module = ModuleResult(name="Login", elapsed="0.10s", status="FAIL", keywords=[keyword])
        passing = ModuleResult(name="Launch App", elapsed="0.10s", status="PASS")
        out = self._rendered(monkeypatch, {
            "TC OK": _TestCaseResult(id="t0", name="TC OK", elapsed="0.10s", status="PASS", modules=[passing]),
            "TC Broken": _TestCaseResult(id="t1", name="TC Broken", elapsed="0.20s", status="FAIL", modules=[module]),
        })

        assert "Failures" in out
        assert "TC Broken › Login › Press Element (btn)" in out
        assert "login_button is not on the screen." in out
        assert "Check that the app shows the screen you expect." in out
        assert "TC OK ›" not in out

    def test_a_passing_run_has_no_failures_panel(self, monkeypatch):
        passing = ModuleResult(name="Launch App", elapsed="0.10s", status="PASS")
        out = self._rendered(monkeypatch, {"TC OK": _TestCaseResult(id="t0", name="TC OK", elapsed="0.10s", status="PASS", modules=[passing])})
        assert "Failures" not in out

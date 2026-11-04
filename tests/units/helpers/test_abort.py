import pytest

from optics_framework.helper.abort import abort_with_panel


@pytest.mark.parametrize("name", ["Pay [red]", "Refund [/red]", "[bold]Login[/bold]"])
def test_abort_panel_shows_bracketed_names_verbatim(name, capsys):
    with pytest.raises(SystemExit) as exc:
        abort_with_panel([f"Unknown test case: '{name}'"])

    assert exc.value.code == 1
    assert name in capsys.readouterr().err

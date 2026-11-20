"""The update notice users see after a command (``optics_framework/helper/updates.py``)."""
import json
import os
import time
from unittest.mock import patch

import pytest

from optics_framework.helper import cli, updates
from optics_framework.helper.environment import EnvKind, Environment

RELEASES = {
    "1.14.0": {"headline": "Bluetooth phones.", "url": "https://example.org/ble"},
    "1.13.0": {"headline": "Desktop apps.", "url": "https://example.org/desktop"},
    "1.12.0": {"headline": "Faster OCR."},
}


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTICS_HOME", str(tmp_path))
    monkeypatch.delenv("OPTICS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setattr(updates, "VERSION", "1.11.0")

    def write(latest, seen="1.11.0", releases=RELEASES, age=0):
        (tmp_path / "update.json").write_text(json.dumps(
            {"checked": time.time() - age, "latest": latest, "seen": seen, "releases": releases}))
    return write


def test_a_newer_release_names_itself_and_the_headlines_of_skipped_releases(cache):
    cache("1.14.0")
    assert updates.notice_lines() == [
        "Optics 1.14.0 is out (you have 1.11.0). Run `optics upgrade`.",
        "  1.14.0: Bluetooth phones. https://example.org/ble",
        "  1.13.0: Desktop apps. https://example.org/desktop",
        "  1.12.0: Faster OCR.",
    ]


def test_what_a_release_added_is_shown_once_after_upgrading(cache, monkeypatch):
    monkeypatch.setattr(updates, "VERSION", "1.13.0")
    cache("1.13.0", seen="1.12.0")
    assert updates.notice_lines() == ["New in Optics 1.13.0: Desktop apps. https://example.org/desktop"]
    assert updates.notice_lines() == []


@pytest.mark.parametrize("variable", ["OPTICS_NO_UPDATE_CHECK", "DO_NOT_TRACK", "CI"])
def test_opting_out_or_running_in_ci_shows_nothing(cache, monkeypatch, variable):
    cache("1.14.0")
    monkeypatch.setenv(variable, "1")
    assert updates.notice_lines() == []


def test_do_not_track_set_to_zero_does_not_opt_out(cache, monkeypatch):
    cache("1.14.0")
    monkeypatch.setenv("DO_NOT_TRACK", "0")
    assert updates.notice_lines()[0].startswith("Optics 1.14.0 is out")


class _Response:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


def _serve(monkeypatch, latest, calls):
    def get(url, **_):
        calls.append(url)
        if latest is None:
            raise updates.requests.ConnectionError("offline")
        return _Response({"info": {"version": latest}} if url == updates.PYPI_URL else {"releases": RELEASES})
    monkeypatch.setattr(updates.requests, "get", get)


def test_a_day_old_check_reads_pypi_again_and_keeps_what_was_already_shown(cache, monkeypatch):
    cache("1.12.0", seen="1.11.0")
    state = json.loads((updates._path()).read_text())
    state["checked"] = time.time() - 2 * 86400
    updates._path().write_text(json.dumps(state))
    calls = []
    _serve(monkeypatch, "1.14.0", calls)
    fresh = updates.refresh("cli")
    assert (fresh.latest, fresh.seen) == ("1.14.0", "1.11.0")
    assert updates.refresh("cli").latest == "1.14.0"
    assert calls == [updates.PYPI_URL, updates.RELEASES_URL]


def test_an_unreachable_pypi_keeps_the_cached_answer(cache, monkeypatch):
    cache("1.12.0", age=2 * 86400)
    calls = []
    _serve(monkeypatch, None, calls)
    assert updates.refresh("cli").latest == "1.12.0"
    assert calls == [updates.PYPI_URL]


def test_a_server_logs_the_notice_once_the_background_check_ends(cache, monkeypatch):
    _serve(monkeypatch, "1.14.0", [])
    monkeypatch.setattr(updates, "_refresh", None)
    logged = []
    updates.refresh_in_background("serve", then=logged.append)
    updates._refresh.join(5)
    assert logged[0] == "Optics 1.14.0 is out (you have 1.11.0). Run `optics upgrade`."


def test_upgrade_on_the_newest_release_runs_nothing(cache, monkeypatch, capsys):
    _serve(monkeypatch, "1.11.0", [])
    monkeypatch.setattr(updates.subprocess, "run", lambda *a, **k: pytest.fail("nothing to upgrade"))
    assert updates.upgrade() == 0
    assert "newest release" in capsys.readouterr().out


def _upgrade_in_ci(monkeypatch, capsys, kind, latest, calls=None):
    """Run `optics upgrade` with CI=1, PyPI answering ``latest`` (None: offline), and the installer recorded."""
    monkeypatch.setenv("CI", "1")
    _serve(monkeypatch, latest, [] if calls is None else calls)
    env = Environment(kind=kind, python="/venv/bin/python", prefix="/venv", has_pip=True, writable=True, manager="uv")
    monkeypatch.setattr(updates, "detect", lambda: env)
    monkeypatch.setattr(updates, "installed_spec", lambda: "optics-framework")
    ran = []
    monkeypatch.setattr(updates.subprocess, "run", lambda command, **_: ran.append(command) or type("Done", (), {"returncode": 0}))
    with patch.object(cli.sys, "argv", ["optics", "upgrade"]), pytest.raises(SystemExit) as exit_:
        cli.main()
    return exit_.value.code, capsys.readouterr().out, ran


def test_upgrade_in_ci_on_the_newest_release_runs_nothing(cache, monkeypatch, capsys):
    code, out, ran = _upgrade_in_ci(monkeypatch, capsys, EnvKind.VENV, "1.11.0")
    assert (code, ran) == (0, [])
    assert out == "Optics 1.11.0 is the newest release.\n"


def test_upgrade_in_ci_pins_a_uv_project_to_the_newest_release(cache, monkeypatch, capsys):
    code, out, _ = _upgrade_in_ci(monkeypatch, capsys, EnvKind.PROJECT, "1.14.0")
    assert code == 1
    assert 'uv add "optics-framework>=1.14.0"' in out


@pytest.mark.skipif(os.name == "nt", reason="Windows prints the upgrade command instead of running it")
@pytest.mark.parametrize(("latest", "said"), [
    ("1.14.0", "Upgrading Optics 1.11.0 to 1.14.0.\n"),
    (None, "Could not look up the newest release on PyPI.\nUpgrading to the newest release the installer finds.\n"),
], ids=["known", "offline"])
def test_upgrade_in_ci_says_what_it_moves_to_before_running_the_installer(cache, monkeypatch, capsys, latest, said):
    code, out, ran = _upgrade_in_ci(monkeypatch, capsys, EnvKind.VENV, latest)
    assert (code, out) == (0, said)
    assert ran[0][-2:] == ["--upgrade", "optics-framework"]


def test_upgrade_offline_says_it_cannot_tell_rather_than_trusting_the_cache(cache, monkeypatch, capsys):
    cache("1.11.0")
    code, out, _ = _upgrade_in_ci(monkeypatch, capsys, EnvKind.PROJECT, None)
    assert code == 1
    assert out.startswith("Could not look up the newest release on PyPI.")
    assert "uv sync --upgrade-package optics-framework" in out


def test_upgrade_with_do_not_track_asks_pypi_only(cache, monkeypatch, capsys):
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    calls = []
    _upgrade_in_ci(monkeypatch, capsys, EnvKind.PROJECT, "1.14.0", calls)
    assert calls == [updates.PYPI_URL]

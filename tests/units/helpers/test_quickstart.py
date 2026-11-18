"""Unit tests for the quickstart wizard (``optics_framework/helper/quickstart.py``).

Every collaborator is mocked: rich prompts are scripted, project creation /
engine install / doctor / onboarding output are stubs. Project paths live
under pytest's tmp_path so even the simulated scaffolding stays hermetic.

The wizard may run the finished project, but only when doctor reports nothing
blocking and the user says yes; ``run_project`` is therefore recorded rather
than forbidden. ``dryrun_main`` stays a never-call guard.
"""
from __future__ import annotations

import io
import os
import types
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from rich.console import Console

from optics_framework.helper import doctor, quickstart

pytestmark = pytest.mark.white_box

MODULE = "optics_framework.helper.quickstart"
EXECUTE_MODULE = "optics_framework.helper.execute"

ANDROID_ANSWERS = {
    "platform": "android", "device_name": "emu-1",
    "platform_name": "Android", "app_package": "com.example.app",
    "app_activity": "com.example.MainActivity",
    "appium_url": "http://127.0.0.1:4723",
    "ocr": False, "log_level": "INFO",
}


class Scripted:
    """Callable returning scripted values in order; fails loudly on overrun.

    Every invocation is recorded (``calls``) so tests can also assert on the
    question text, which never reaches ``out`` — rich renders prompts to the
    real console, not this module's buffered one."""

    def __init__(self, values):
        self._values = list(values)
        self.calls = []

    def __call__(self, *args, **kwargs):
        if not self._values:
            raise AssertionError("prompt called more times than scripted")
        self.calls.append((args, kwargs))
        return self._values.pop(0)


class AlwaysEnter:
    """A user who only ever presses Enter, emulating ``rich.Prompt.ask``.

    Rich returns the question's ``default`` on empty input and ``""`` when the
    question has none, so every prompt the wizard reaches on this path must
    carry a usable default or the wizard cannot progress. Calls are capped so a
    regression fails the test instead of hanging the suite."""

    def __init__(self, cap=20):
        self.cap = cap
        self.calls = []

    def __call__(self, question, **kwargs):
        self.calls.append(question)
        if len(self.calls) > self.cap:
            raise AssertionError(
                f"prompt loop did not terminate within {self.cap} questions")
        return kwargs.get("default", "")


class Spy:
    """Recording stand-in for functions the wizard must never call."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class Harness:
    """Patch every side-effecting collaborator and script the prompts.

    ``spies`` records what the wizard did; ``out`` holds what it printed. The
    fake ``create_project`` mirrors the real function by leaving a config.yaml
    behind whenever a sample template was chosen."""

    def __init__(self, ask_values, confirm_values, *,
                 answers=None,
                 install_result=(True, "Engine packages installed."),
                 start_from_sample=False, ready=False, run_first=False,
                 run_passes=True):
        self.out = io.StringIO()
        self.prompts = Scripted(ask_values)
        # The wizard asks about the domain's sample between the engine-install
        # question and everything else, and about a first run only when doctor
        # came back ready. Both are spliced in here rather than written into
        # every caller's list, so a test's own booleans keep their meaning.
        confirms = list(confirm_values)
        confirms = [*confirms[:1], start_from_sample, *confirms[1:]]
        if ready:
            confirms.append(run_first)
        self.confirms = Scripted(confirms)
        self.spies = types.SimpleNamespace(
            create_project=None, create_project_kwargs=None,
            write=None, diagnose=None, what_next=None,
            next_steps=None, install=None, prompt_domain=None,
            execute_main=Spy(), dryrun_main=Spy())
        answers = answers or ANDROID_ANSWERS
        stack = self._stack = ExitStack()

        def fake_write(folder, text):
            self.spies.write = (folder, text)
            return os.path.join(folder, "config.yaml")

        def fake_create(args, *, show_next_steps=True, pick_template=True):
            self.spies.create_project = args
            self.spies.create_project_kwargs = {
                "show_next_steps": show_next_steps,
                "pick_template": pick_template,
            }
            if args.template:
                folder = os.path.join(args.path, args.name)
                os.makedirs(folder, exist_ok=True)
                with open(os.path.join(folder, "config.yaml"), "w",
                          encoding="utf-8"):
                    pass

        def fake_diagnose(folder=None):
            self.spies.diagnose = folder
            # `ready` is the property; a blocking hint is what makes it False,
            # exactly as a missing device or unstarted Appium server would.
            return doctor.Diagnosis(
                sections=[], blocking=[] if ready else ["connect a device"],
                failed=False)

        def fake_print_diagnosis(_diagnosis):
            pass

        def fake_run_project(folder, *_a, **_k):
            self.spies.execute_main(folder)
            return run_passes

        def fake_what_next(path):
            self.spies.what_next = path

        def fake_next_steps(path, **kwargs):
            self.spies.next_steps = (path, kwargs)

        def fake_install(requests):
            self.spies.install = requests
            return install_result

        def fake_prompt(*, domain=None):
            self.spies.prompt_domain = domain
            return dict(answers)

        for target, replacement in [
            (f"{MODULE}._console", Console(file=self.out, width=200)),
            (f"{MODULE}.Prompt.ask", self.prompts),
            (f"{MODULE}.Confirm.ask", self.confirms),
            (f"{MODULE}.project_config.prompt_project_config", fake_prompt),
            (f"{MODULE}.project_config.render_project_config",
             lambda a: "rendered-config"),
            (f"{MODULE}.project_config.write_project_config", fake_write),
            (f"{MODULE}.initialize.create_project", fake_create),
            (f"{MODULE}.initialize.available_templates",
             lambda: ["contact", "playwright"]),
            (f"{MODULE}.doctor.diagnose", fake_diagnose),
            (f"{MODULE}.doctor.print_diagnosis", fake_print_diagnosis),
            (f"{MODULE}.onboarding.welcome", lambda *a, **k: None),
            (f"{MODULE}.onboarding.is_first_run", lambda: False),
            (f"{MODULE}.onboarding.print_next_steps", fake_next_steps),
            (f"{MODULE}.onboarding.print_what_next", fake_what_next),
            (f"{MODULE}.resolve_engines", _fake_resolve),
            (f"{MODULE}.install_extras", fake_install),
            # run_project is recorded rather than forbidden: the wizard may
            # run the project, but only when asked to. dryrun_main stays a
            # never-call guard.
            (f"{EXECUTE_MODULE}.run_project", fake_run_project),
            (f"{EXECUTE_MODULE}.dryrun_main", self.spies.dryrun_main),
        ]:
            stack.enter_context(patch(target, replacement))

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self._stack.close()

    def run(self):
        quickstart.run_quickstart()


def _fake_resolve(tokens):
    """Mirror setup.resolve_engines: bundle tokens expand to their engines."""
    bundles = {"mobile": ["Appium"], "web": ["Playwright", "Selenium"]}
    names: list[str] = []
    for token in tokens:
        names.extend(bundles.get(token, [token]))
    return ([types.SimpleNamespace(engine=types.SimpleNamespace(name=name))
             for name in names], [])


# --------------------------------------------------------------------------- #
# Happy path                                                                   #
# --------------------------------------------------------------------------- #

class TestHappyPath:

    def test_writes_rendered_config_into_project(self, tmp_path):
        with Harness(["mobile", "1", "demo", str(tmp_path)], [True]) as h:
            h.run()
        assert h.spies.write == (str(tmp_path / "demo"), "rendered-config")


# --------------------------------------------------------------------------- #
# Domain threading into config building                                        #
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Engine installation outcomes                                                 #
# --------------------------------------------------------------------------- #

class TestEngineInstall:

    def test_desktop_installs_the_appium_engine(self, tmp_path):
        with Harness(["desktop", "1", "demo", str(tmp_path)], [False]) as h:
            h.run()
        assert "optics setup --install appium" in h.out.getvalue()

    def test_failed_install_surfaces_the_reason_and_keeps_going(self, tmp_path):
        """install_extras owns the remedy (it knows the environment); the
        wizard only has to show it and still deliver the project."""
        with Harness(["mobile", "1", "demo", str(tmp_path)], [True],
                     install_result=(False, "Installation failed: boom")) as h:
            h.run()  # must not raise
        assert "boom" in h.out.getvalue()
        assert h.spies.create_project is not None
        assert h.spies.diagnose is not None


# --------------------------------------------------------------------------- #
# Template handling                                                            #
# --------------------------------------------------------------------------- #


class TestTemplateDomainMismatch:
    """A mobile-only sample offered to a web user (or vice versa) must be
    flagged, not silently scaffolded into the wrong engine's config.

    The question text lives in the recorded Confirm.ask calls — rich renders
    prompts to the real console, not the wizard's buffered one."""

    def _mismatch_questions(self, h):
        return [args[0] for args, _ in h.confirms.calls if "sample targets" in str(args)]

    def test_mismatch_declined_loops_back_to_template_choice(self, tmp_path):
        with Harness(["web", "2", "1", "demo", str(tmp_path)], [True, False]) as h:
            h.run()
        assert len(self._mismatch_questions(h)) == 1
        assert h.spies.create_project.template is None


# --------------------------------------------------------------------------- #
# Existing project directory                                                   #
# --------------------------------------------------------------------------- #

class TestExistingProject:
    """The wizard must never write into an already-existing project."""

    def test_suggested_name_skips_suffixes_already_taken(
            self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        for suffix in ("", "-2", "-3"):
            (tmp_path / f"{quickstart._DEFAULT_NAME}{suffix}").mkdir()
        with Harness([], [True]) as h:
            with patch(f"{MODULE}.Prompt.ask", AlwaysEnter()):
                h.run()
        assert h.spies.create_project.name == f"{quickstart._DEFAULT_NAME}-4"

    def test_eof_on_rename_prompt_aborts_without_touching_existing(self, tmp_path):
        existing = tmp_path / "demo"
        existing.mkdir()
        (existing / "config.yaml").write_text("keep", encoding="utf-8")
        with Harness(["mobile", "1", "demo", str(tmp_path)], [True]) as h:
            with patch(f"{MODULE}.Prompt.ask",
                       side_effect=["mobile", "1", "demo", str(tmp_path),
                                    EOFError]):
                with pytest.raises(SystemExit) as exc:
                    h.run()
        assert exc.value.code == 1
        assert (existing / "config.yaml").read_text(encoding="utf-8") == "keep"
        assert h.spies.create_project is None
        assert h.spies.write is None


class TestFirstRun:
    def test_ready_and_accepted_runs_then_shows_what_next(self, tmp_path):
        with Harness(["mobile", "demo", str(tmp_path)], [True, False],
                     start_from_sample=True, ready=True, run_first=True) as h:
            h.run()
        folder = str(tmp_path / "demo")
        assert h.spies.execute_main.calls == [((folder,), {})]
        assert h.spies.dryrun_main.calls == []
        assert h.spies.what_next == folder
        assert h.spies.next_steps is None

"""Unit tests for the quickstart wizard (``optics_framework/helper/quickstart.py``).

Every collaborator is mocked: rich prompts are scripted, project creation /
engine install / doctor / onboarding output are stubs. Project paths live
under pytest's tmp_path so even the simulated scaffolding stays hermetic. The
central guarantee under test is that the wizard scaffolds and advises — it
must never invoke ``execute_main`` or ``dryrun_main``, which are patched with
recording spies.
"""
from __future__ import annotations

import io
import os
import types
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from rich.console import Console

from optics_framework.helper import quickstart

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
                 install_result=(True, "Engine packages installed.")):
        self.out = io.StringIO()
        self.prompts = Scripted(ask_values)
        self.confirms = Scripted(confirm_values)
        self.spies = types.SimpleNamespace(
            create_project=None, create_project_kwargs=None,
            write=None, run_doctor=None,
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

        def fake_doctor(*args, **kwargs):
            self.spies.run_doctor = (args, kwargs)

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
            (f"{MODULE}.initialize.available_templates", lambda: ["contact"]),
            (f"{MODULE}.doctor.run_doctor", fake_doctor),
            (f"{MODULE}.onboarding.welcome", lambda *a, **k: None),
            (f"{MODULE}.onboarding.is_first_run", lambda: False),
            (f"{MODULE}.onboarding.print_next_steps", fake_next_steps),
            (f"{MODULE}.resolve_engines", _fake_resolve),
            (f"{MODULE}.install_extras", fake_install),
            # Guards: the wizard must never drive or dry-run anything itself.
            (f"{EXECUTE_MODULE}.execute_main", self.spies.execute_main),
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
    def test_creates_project_with_expected_args(self, tmp_path):
        base = str(tmp_path)
        with Harness(["mobile", "1", "demo", base], [True]) as h:
            h.run()
        args = h.spies.create_project
        assert (args.name, args.path) == ("demo", base)
        assert (args.force, args.template, args.git_init) == (False, None, False)
        # The wizard owns both embedded-call concerns: no re-prompting picker,
        # and no next-steps block (its own final block is the single source).
        assert h.spies.create_project_kwargs == {
            "show_next_steps": False, "pick_template": False}

    def test_writes_rendered_config_into_project(self, tmp_path):
        with Harness(["mobile", "1", "demo", str(tmp_path)], [True]) as h:
            h.run()
        assert h.spies.write == (str(tmp_path / "demo"), "rendered-config")

    def test_verifies_with_doctor_then_prints_next_steps(self, tmp_path):
        folder = str(tmp_path / "demo")
        with Harness(["mobile", "1", "demo", str(tmp_path)], [True]) as h:
            h.run()
        assert h.spies.run_doctor == ((), {"folder": folder})
        assert h.spies.next_steps == (folder, {"configured": True})

    def test_installs_mobile_engine_for_mobile_domain(self, tmp_path):
        with Harness(["mobile", "1", "demo", str(tmp_path)], [True]) as h:
            h.run()
        assert [r.engine.name for r in h.spies.install] == ["Appium"]

    def test_never_invokes_execute_or_dry_run(self, tmp_path):
        with Harness(["web", "1", "demo", str(tmp_path)], [True]) as h:
            h.run()
        assert h.spies.execute_main.calls == []
        assert h.spies.dryrun_main.calls == []


class TestWebDomain:
    def test_offers_both_web_engines(self, tmp_path):
        with Harness(["web", "1", "site", str(tmp_path)], [True]) as h:
            h.run()
        assert sorted(r.engine.name for r in h.spies.install) == [
            "Playwright", "Selenium"]


# --------------------------------------------------------------------------- #
# Domain threading into config building                                        #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("domain", ["mobile", "web"])
def test_chosen_domain_is_passed_to_prompt_project_config(tmp_path, domain):
    # prompt_project_config itself restricts the platform choices to the
    # domain (asserted in test_project_config); here we pin that the wizard
    # actually forwards its answer.
    with Harness([domain, "1", "demo", str(tmp_path)], [True]) as h:
        h.run()
    assert h.spies.prompt_domain == domain


# --------------------------------------------------------------------------- #
# Engine installation outcomes                                                 #
# --------------------------------------------------------------------------- #

class TestEngineInstall:
    def test_declined_install_skips_install_extras(self, tmp_path):
        with Harness(["mobile", "1", "demo", str(tmp_path)], [False]) as h:
            h.run()
        assert h.spies.install is None
        # The domain doubles as a `setup` bundle token — the hint must stay
        # copy-pasteable.
        assert "optics setup --install mobile" in h.out.getvalue()

    def test_failed_install_prints_message_and_virtualenv_hint(self, tmp_path):
        with Harness(["mobile", "1", "demo", str(tmp_path)], [True],
                     install_result=(False, "Installation failed: boom")) as h:
            h.run()  # must not raise
        out = h.out.getvalue()
        assert "boom" in out
        assert "virtualenv" in out.lower()

    def test_success_message_shown(self, tmp_path):
        with Harness(["mobile", "1", "demo", str(tmp_path)], [True],
                     install_result=(True,
                                     "Engine packages installed.")) as h:
            h.run()
        assert "Engine packages installed" in h.out.getvalue()


# --------------------------------------------------------------------------- #
# Template handling                                                            #
# --------------------------------------------------------------------------- #

class TestTemplates:
    def test_sample_template_choice_is_forwarded(self, tmp_path):
        # Menu numbering: 1 = blank, 2 = 'contact' (stubbed templates list).
        # Confirms: install engines? yes; overwrite template config? yes.
        with Harness(["mobile", "2", "demo", str(tmp_path)],
                     [True, True]) as h:
            h.run()
        assert h.spies.create_project.template == "contact"

    def test_blank_choice_maps_to_no_template(self, tmp_path):
        with Harness(["mobile", "1", "demo", str(tmp_path)], [True]) as h:
            h.run()
        assert h.spies.create_project.template is None

    def test_template_config_kept_when_overwrite_declined(self, tmp_path):
        # Confirms: 1) install engines? yes  2) overwrite template config? no.
        with Harness(["mobile", "2", "demo", str(tmp_path)], [True, False]) as h:
            h.run()
        assert h.spies.write is None
        assert "Keeping the template's config" in h.out.getvalue()
        # The journey still finishes after keeping the template's config.
        assert h.spies.next_steps == (str(tmp_path / "demo"),
                                      {"configured": True})

    def test_blank_project_writes_config_without_overwrite_prompt(self, tmp_path):
        # Blank scaffolding also produces a starter config.yaml; only the
        # template branch may ask before overwriting it.
        with Harness(["mobile", "1", "demo", str(tmp_path)], [True]) as h:
            h.run()
        assert h.spies.write is not None


class TestTemplateDomainMismatch:
    """A mobile-only sample offered to a web user (or vice versa) must be
    flagged, not silently scaffolded into the wrong engine's config.

    The question text lives in the recorded Confirm.ask calls — rich renders
    prompts to the real console, not the wizard's buffered one."""

    def _mismatch_questions(self, h):
        return [args[0] for args, _ in h.confirms.calls if "sample targets" in str(args)]

    def test_mismatch_is_flagged_and_accepted_on_confirm(self, tmp_path):
        with Harness(["web", "2", "demo", str(tmp_path)], [True, True, True]) as h:
            h.run()
        questions = self._mismatch_questions(h)
        assert len(questions) == 1
        assert "The 'contact' sample targets mobile" in questions[0]
        assert "but you chose web" in questions[0]
        assert "Use it anyway?" in questions[0]
        assert h.spies.create_project.template == "contact"

    def test_mismatch_declined_loops_back_to_template_choice(self, tmp_path):
        with Harness(["web", "2", "1", "demo", str(tmp_path)], [True, False]) as h:
            h.run()
        assert len(self._mismatch_questions(h)) == 1
        assert h.spies.create_project.template is None

    def test_web_sample_offered_to_mobile_user_is_flagged(self, tmp_path):
        with Harness(["mobile", "3", "1", "demo", str(tmp_path)], [True, False]) as h:
            with patch(f"{MODULE}.initialize.available_templates",
                       return_value=["contact", "gmail_web"]):
                h.run()
        questions = self._mismatch_questions(h)
        assert len(questions) == 1
        assert "The 'gmail_web' sample targets web" in questions[0]
        assert h.spies.create_project.template is None

    def test_matching_sample_never_asks(self, tmp_path):
        with Harness(["mobile", "2", "demo", str(tmp_path)],
                     [True, True]) as h:
            h.run()
        assert self._mismatch_questions(h) == []

    def test_blank_choice_never_asks(self, tmp_path):
        with Harness(["web", "1", "demo", str(tmp_path)], [True]) as h:
            h.run()
        assert self._mismatch_questions(h) == []


# --------------------------------------------------------------------------- #
# Existing project directory                                                   #
# --------------------------------------------------------------------------- #

class TestExistingProject:
    """The wizard must never write into an already-existing project."""

    def test_existing_dir_reprompts_and_uses_new_name(self, tmp_path):
        existing = tmp_path / "demo"
        existing.mkdir()
        (existing / "config.yaml").write_text("keep", encoding="utf-8")
        # Prompts: domain, template, name (collides), base, re-prompt (new).
        with Harness(["mobile", "1", "demo", str(tmp_path), "fresh"],
                     [True]) as h:
            h.run()
        args = h.spies.create_project
        assert (args.name, args.path) == ("fresh", str(tmp_path))
        assert (existing / "config.yaml").read_text(encoding="utf-8") == "keep"
        assert h.spies.write == (str(tmp_path / "fresh"), "rendered-config")
        out = h.out.getvalue()
        assert "already exists" in out
        # quickstart has no --force flag, so its warning must not mention one.
        assert "--force" not in out

    def test_enter_through_collision_lands_on_a_free_name(
            self, tmp_path, monkeypatch):
        # The default project name already exists and the user answers every
        # question by pressing Enter. The re-prompt must offer a free name as
        # its default, otherwise the wizard spins forever between "already
        # exists" and an empty answer.
        monkeypatch.chdir(tmp_path)  # the base-path question defaults to cwd
        existing = tmp_path / quickstart._DEFAULT_NAME
        existing.mkdir()
        (existing / "config.yaml").write_text("keep", encoding="utf-8")
        with Harness([], [True]) as h:
            with patch(f"{MODULE}.Prompt.ask", AlwaysEnter()):
                h.run()
        args = h.spies.create_project
        assert args.name == f"{quickstart._DEFAULT_NAME}-2"
        assert not os.path.exists(os.path.join(args.path, args.name))
        assert (existing / "config.yaml").read_text(encoding="utf-8") == "keep"

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


# --------------------------------------------------------------------------- #
# Guard                                                                        #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "domain, template_choice",
    [("mobile", "1"), ("web", "1"), ("web", "2")],
)
def test_wizard_completes_without_execute_or_dry_run(
        tmp_path, domain, template_choice):
    mismatch_confirm = template_choice != "1" and domain == "web"
    overwrite_confirm = template_choice != "1"
    confirms = [True]
    if mismatch_confirm:
        confirms.append(True)
    if overwrite_confirm:
        confirms.append(True)
    with Harness([domain, template_choice, "demo", str(tmp_path)],
                 confirms) as h:
        h.run()
    assert h.spies.execute_main.calls == []
    assert h.spies.dryrun_main.calls == []

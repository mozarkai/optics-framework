"""``optics quickstart`` — the guided golden path for beginners.

One command walks a newcomer from "nothing installed" to a runnable project:
welcome → pick a domain → install the right engine → scaffold the project →
answer a short config Q&A → doctor verifies everything → next steps.

The wizard offers to run the new project once, but only after doctor reports
nothing blocking and only behind a yes/no — the first execution stays an
intentional, visible act, it just no longer has to be typed out by hand.
``dry_run`` is never invoked; it stays a printed next move.
"""
import os
import types

from rich.console import Console
from rich.prompt import Confirm, Prompt

from optics_framework.helper import (doctor, execute, initialize, onboarding,
                                     project_config)
from optics_framework.helper.setup import install_extras, resolve_engines

_console = Console()

_DEFAULT_NAME = "my-optics-project"

# Playwright needs only a browser, so a web user reaches a passing test with no
# hardware at all.
_DOMAIN_SAMPLE = {"mobile": "contact", "web": "playwright"}

_TEMPLATE_DOMAINS = {
    "calendar": "mobile",
    "clock": "mobile",
    "contact": "mobile",
    "youtube": "mobile",
    "gmail_web": "web",
    "playwright": "web",
}


def run_quickstart() -> None:
    """Run the full guided journey end to end."""
    onboarding.welcome(first_run=onboarding.is_first_run())
    domain = _ask_domain()
    _offer_engine_install(domain)
    template = _choose_start(domain)
    while template is not None and _TEMPLATE_DOMAINS.get(template, domain) != domain:
        target_domain = _TEMPLATE_DOMAINS[template]
        onboarding.blank_line()
        if Confirm.ask(
            f"The '{template}' sample targets {target_domain}, but you chose "
            f"{domain}. Use it anyway?", default=False):
            break
        template = _choose_template()
    onboarding.blank_line()
    name = Prompt.ask(
        "Project name", default=_DEFAULT_NAME).strip() or _DEFAULT_NAME
    onboarding.blank_line()
    base_path = Prompt.ask("Where should the project live?", default=os.getcwd())
    project_path = os.path.join(base_path, name)
    while os.path.exists(project_path):
        onboarding.blank_line()
        # Every re-prompt offers a free name as its default, so hitting Enter
        # always advances towards an available path instead of re-submitting
        # the colliding one.
        suggestion = _suggest_free_name(base_path, name)
        _console.print(
            f"Project '{project_path}' already exists. Choose a different name.")
        try:
            name = Prompt.ask(
                "Project name", default=suggestion).strip() or suggestion
        except EOFError:
            _console.print("Aborted; the existing project was left untouched.")
            raise SystemExit(1)
        project_path = os.path.join(base_path, name)

    # The wizard resolved its template question above and prints its own next
    # steps below (configured=True — it just wrote the user's answers into
    # config.yaml), so suppress create_project's picker and guidance block:
    # pick_template=False makes template=None deterministically mean "blank"
    # instead of re-prompting on a TTY, and show_next_steps=False avoids a
    # duplicate — for blank projects contradictory — advice block.
    initialize.create_project(types.SimpleNamespace(
        name=name, path=base_path, force=False, template=template, git_init=False),
        show_next_steps=False, pick_template=False)

    _build_config(project_path, template, domain)
    diagnosis = doctor.diagnose(project_path)
    doctor.print_diagnosis(diagnosis)
    if diagnosis.ready and _offer_first_run(project_path):
        onboarding.print_what_next(project_path)
    else:
        onboarding.print_next_steps(project_path, configured=True)


def _offer_first_run(project_path: str) -> bool:
    """Offer to run the new project once; True when the run passed.

    Only reached when doctor found nothing blocking, so the wizard never
    invites a run it already knows will fail."""
    onboarding.blank_line()
    if not Confirm.ask("Run your first test now?", default=True):
        return False
    return _run_project(project_path)


def _run_project(project_path: str) -> bool:
    """Run the project, surviving a run that goes wrong.

    A device dropping mid-test raises out of the runner, which would take the
    wizard's closing guidance down with it — and a failed first run is when a
    newcomer most needs to be told what to do next."""
    try:
        return execute.run_project(project_path)
    except Exception as error:
        _console.print(f"The run stopped early: {error}")
        return False


def _suggest_free_name(base_path: str, name: str) -> str:
    """First ``name-N`` under ``base_path`` that nothing occupies yet."""
    suffix = 2
    while os.path.exists(os.path.join(base_path, f"{name}-{suffix}")):
        suffix += 1
    return f"{name}-{suffix}"


def _ask_domain() -> str:
    onboarding.blank_line()
    return Prompt.ask(
        "What do you want to automate?", choices=["mobile", "web"],
        default="mobile")


def _offer_engine_install(domain: str) -> None:
    """Offer to pip-install the engines the chosen domain needs.

    The domain doubles as a ``setup`` bundle token ("mobile"/"web"), so engine
    resolution keeps a single source of truth in setup.py's bundles — this
    module never re-lists engines.

    A failed install never aborts the wizard. ``install_extras`` already
    explains an environment that refuses the install and names the command that
    would work there, so this only adds that the wizard is carrying on."""
    requests, invalid = resolve_engines([domain])
    if invalid or not requests:  # defensive: the domain is a fixed bundle token
        return
    names = ", ".join(sorted({req.engine.name for req in requests}))
    onboarding.blank_line()
    if not Confirm.ask(f"Install {names} now?", default=True):
        _console.print(f"No problem — install later with:  "
                       f"optics setup --install {domain}")
        return
    success, message = install_extras(requests)
    _console.print(message)
    if not success:
        _console.print(
            "\nCarrying on — your answers so far are kept, and the project is "
            "still created.")


def _choose_start(domain: str) -> str | None:
    """Sample to start from, or None for a blank project.

    Offering the domain's own sample as a yes/no keeps the common path to one
    keystroke; the full list is shown only to whoever declines it."""
    sample = _DOMAIN_SAMPLE.get(domain)
    if sample and sample in initialize.available_templates():
        onboarding.blank_line()
        if Confirm.ask(f"Start from the '{sample}' example, so you have a test "
                       "that runs straight away?", default=True):
            return sample
    return _choose_template()


def _choose_template() -> str | None:
    """Offer every packaged sample plus a blank start. Returns None for blank."""
    templates = initialize.available_templates()
    options = ["blank", *templates]
    onboarding.blank_line()
    _console.print("Pick a starting point:")
    for number, option in enumerate(options, start=1):
        label = ("An empty project" if option == "blank"
                 else f"The '{option}' sample project")
        _console.print(f"  {number}. {label}")
    choice = Prompt.ask(
        "Template number",
        choices=[str(i) for i in range(1, len(options) + 1)], default="1")
    index = int(choice) - 1
    return None if index == 0 else options[index]


def _build_config(project_path: str, template: str | None, domain: str) -> None:
    """Q&A-render-write the project's config.yaml.

    The wizard's already-chosen ``domain`` constrains the platform question —
    a mobile user must not be able to pick web-selenium and end up with an
    engine they never installed.

    write_project_config overwrites unconditionally, so the wizard owns the
    confirm-before-overwrite: a sample template ships a curated, runnable
    config and must not lose it to a silent regeneration."""
    config_path = os.path.join(project_path, "config.yaml")
    if template is not None and os.path.isfile(config_path):
        onboarding.blank_line()
        # A mobile sample pins one particular emulator, so its config almost
        # always needs replacing; the browser sample runs exactly as shipped.
        if not Confirm.ask(
            f"'{template}' ships its own working config.yaml. Overwrite it "
            "with your own answers?", default=domain == "mobile"):
            _console.print(f"Keeping the template's config at {config_path}.")
            return
    answers = project_config.prompt_project_config(domain=domain)
    text = project_config.render_project_config(answers)
    written = project_config.write_project_config(project_path, text)
    _console.print(f"Wrote [bold]{written}[/bold]")

"""``optics quickstart`` — the guided golden path for beginners.

One command walks a newcomer from "nothing installed" to a runnable project:
welcome → pick a domain → install the right engine → scaffold the project →
answer a short config Q&A → doctor verifies everything → next steps.

The wizard scaffolds and explains; it deliberately NEVER runs ``dry_run`` or
``execute`` itself — those are printed as the user's next moves so the first
execution is always an intentional, visible act.
"""
import os
import types

from rich.console import Console
from rich.prompt import Confirm, Prompt

from optics_framework.helper import doctor, initialize, onboarding, project_config
from optics_framework.helper.setup import install_extras, resolve_engines

_console = Console()

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
    template = _choose_template()
    while template is not None and _TEMPLATE_DOMAINS.get(template, domain) != domain:
        target_domain = _TEMPLATE_DOMAINS[template]
        if Confirm.ask(
            f"The '{template}' sample targets {target_domain}, but you chose "
            f"{domain}. Use it anyway?", default=False):
            break
        template = _choose_template()
    name = Prompt.ask("Project name", default="my-optics-project")
    base_path = Prompt.ask("Where should the project live?", default=os.getcwd())
    project_path = os.path.join(base_path, name)
    while os.path.exists(project_path):
        _console.print(
            f"Project '{project_path}' already exists. Choose a different name.")
        try:
            name = Prompt.ask("Project name").strip()
        except EOFError:
            _console.print("Aborted; the existing project was left untouched.")
            raise SystemExit(1)
        if not name:
            _console.print("A project name can't be empty.")
            continue
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
    doctor.run_doctor(folder=project_path)
    onboarding.print_next_steps(project_path, configured=True)


def _ask_domain() -> str:
    return Prompt.ask(
        "What do you want to automate?", choices=["mobile", "web"],
        default="mobile")


def _offer_engine_install(domain: str) -> None:
    """Offer to pip-install the engines the chosen domain needs.

    The domain doubles as a ``setup`` bundle token ("mobile"/"web"), so engine
    resolution keeps a single source of truth in setup.py's bundles — this
    module never re-lists engines.

    A failed install never aborts the wizard — the most common cause (PEP 668
    externally-managed Python) just needs a virtualenv, so say that plainly."""
    requests, invalid = resolve_engines([domain])
    if invalid or not requests:  # defensive: the domain is a fixed bundle token
        return
    names = ", ".join(sorted({req.engine.name for req in requests}))
    if not Confirm.ask(f"Install {names} now?", default=True):
        _console.print(f"No problem — install later with:  "
                       f"optics setup --install {domain}")
        return
    success, message = install_extras(requests)
    _console.print(message)
    if not success:
        _console.print(
            "\nIf pip complained about an [bold]externally managed environment"
            "[/bold], put optics in a virtualenv first:\n"
            "  python3 -m venv .venv\n"
            "  source .venv/bin/activate\n"
            "  pip install optics-framework\n"
            "Then re-run this step — your answers so far are kept.")


def _choose_template() -> str | None:
    """Offer every packaged sample plus a blank start. Returns None for blank."""
    templates = initialize.available_templates()
    options = ["blank", *templates]
    _console.print("Pick a starting point:")
    for number, option in enumerate(options, start=1):
        label = ("An empty project (recommended)" if option == "blank"
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
        if not Confirm.ask(
            f"'{template}' ships its own working config.yaml. Overwrite it "
            "with your own answers?", default=False):
            _console.print(f"Keeping the template's config at {config_path}.")
            return
    answers = project_config.prompt_project_config(domain=domain)
    text = project_config.render_project_config(answers)
    written = project_config.write_project_config(project_path, text)
    _console.print(f"Wrote [bold]{written}[/bold]")

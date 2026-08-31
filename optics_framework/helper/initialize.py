import os
import json
import shutil
import subprocess # nosec
import pathlib
import sys

from optics_framework.helper.onboarding import blank_line, print_next_steps


# Files/directories that must never be copied out of a sample template.
_SKIP_NAMES = {"__pycache__"}


def _is_junk(name: str) -> bool:
    """True for hidden files (e.g. .DS_Store) and build cruft."""
    return name.startswith(".") or name in _SKIP_NAMES


def _samples_dir() -> pathlib.Path:
    return pathlib.Path(__file__).parent.parent / "samples"


def available_templates() -> list[str]:
    """Sample project names shippable via ``optics init --template``, discovered
    from the packaged ``samples/`` directory. Single source of truth for the
    ``--template`` help text and the shell-completion candidate lists."""
    samples = _samples_dir()
    if not samples.is_dir():
        return []
    return sorted(
        p.name for p in samples.iterdir()
        if p.is_dir() and not _is_junk(p.name)
    )


def _template_choices() -> list[tuple[str, str]]:
    """Ordered ``(display_name, folder_name)`` choices for the interactive picker.

    Built from ``samples/metadata.json`` (the ``displayName`` field) so the
    picker shows friendly names ("Contacts") while still mapping back to the
    underlying sample folder ("contact"). A "Blank project" sentinel is prepended
    so a user can opt out of a template interactively. Falls back to folder names
    if the metadata file is missing or malformed."""
    choices: list[tuple[str, str]] = [("(Blank project)", "")]
    metadata_path = _samples_dir() / "metadata.json"
    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)
        for sample in metadata.get("samples", []):
            display = sample.get("displayName")
            folder = sample.get("folderName")
            if display and folder:
                choices.append((display, folder))
    except (OSError, json.JSONDecodeError):
        # Metadata missing/corrupt: fall back to bare folder names.
        for folder in available_templates():
            choices.append((folder, folder))
    return choices


def _pick_template() -> str | None:
    """Prompt the user to choose a template interactively.

    Returns the chosen ``folderName`` (or ``""`` for Blank) on a clean pick,
    or ``None`` if the user backs out. Only called when stdin is a TTY."""
    choices = _template_choices()
    blank_line()
    print("Choose a starting template:")
    for idx, (display, _) in enumerate(choices, start=1):
        print(f"  {idx}. {display}")
    while True:
        raw = input(f"Select [1-{len(choices)}] (blank to cancel): ").strip()
        if not raw:
            return None
        try:
            idx = int(raw)
        except ValueError:
            blank_line()
            print("Please enter a number.")
            continue
        if 1 <= idx <= len(choices):
            return choices[idx - 1][1]
        blank_line()
        print(f"Enter a number between 1 and {len(choices)}.")


# A commented starter config for `optics init` without a template. It mirrors the
# framework defaults (every source present but disabled) and tells the user how to
# turn one on. Enable exactly one driver and at least one matching elements_source.
_STARTER_CONFIG = """\
# Optics Framework project configuration.
#
# Getting started:
#   1. Enable ONE driver under driver_sources (set enabled: true) and fill in its
#      capabilities/url.
#   2. Enable the matching elements_sources for that driver.
#   3. Install the driver's packages, e.g.  optics setup --install appium
#
# Full reference: https://mozarkai.github.io/optics-framework/configuration/

driver_sources:
  # Native Android/iOS via Appium. Needs a running Appium server and a connected
  # device/emulator.  Install:  optics setup --install appium
  - appium:
      enabled: false
      url: "http://localhost:4723"
      capabilities:
        appPackage: com.example.app
        appActivity: com.example.app.MainActivity
        automationName: UiAutomator2
        deviceName: emulator-5554
        platformName: Android
  # Web via a Selenium/WebDriver server.  Install:  optics setup --install selenium
  - selenium:
      enabled: false
      url: "http://localhost:4444/wd/hub"
      capabilities: {}
  # Web via Playwright (no external server).  Install:  optics setup --install playwright
  - playwright:
      enabled: false
      capabilities:
        browser: chromium
        headless: false

elements_sources:
  # Appium locators / page source / screenshots:
  - appium_find_element:
      enabled: false
  - appium_page_source:
      enabled: false
  - appium_screenshot:
      enabled: false
  # Selenium locators / page source / screenshots:
  - selenium_find_element:
      enabled: false
  - selenium_page_source:
      enabled: false
  - selenium_screenshot:
      enabled: false
  # Playwright locators / page source / screenshots:
  - playwright_find_element:
      enabled: false
  - playwright_page_source:
      enabled: false
  - playwright_screenshot:
      enabled: false

# Optional vision fallbacks: locate elements by on-screen text (OCR) or image.
text_detection:
  - easyocr:            # install: optics setup --install easyocr
      enabled: false
image_detection:
  - templatematch:
      enabled: false

log_level: INFO
json_log: true
file_log: true
"""


def _check_and_prepare_directory(project_path: str, force: bool) -> None:
    """Check if project directory exists and prepare it based on force flag.

    Raises ``ValueError`` when the path exists and ``force`` is False, so the
    CLI maps it to a non-zero exit instead of silently skipping creation."""
    if os.path.exists(project_path):
        if force:
            shutil.rmtree(project_path)
            print(
                f"Existing project folder removed due to --force: {project_path}")
        else:
            raise ValueError(
                f"Project '{project_path}' already exists."
                " Use --force to delete it and recreate the project.")
    os.makedirs(project_path)
    print(f"Created project directory: {project_path}")


def _scaffold_project(project_path: str) -> None:
    """Create an empty-but-runnable project skeleton (subdir layout matching the
    samples) plus a commented starter config."""
    files = {
        os.path.join("test_cases", "test_cases.csv"): "test_case,test_step\n",
        os.path.join("modules", "modules.csv"): "module_name,module_step,param_1,param_2,param_3\n",
        os.path.join("test_data", "elements.csv"): "Element_Name,Element_ID\n",
    }
    for rel_path, content in files.items():
        file_path = os.path.join(project_path, rel_path)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

    with open(os.path.join(project_path, "config.yaml"), "w", encoding="utf-8") as f:
        f.write(_STARTER_CONFIG)
    print("Created starter project (test_cases/, modules/, test_data/, config.yaml).")


def _copy_template(project_path: str, template: str) -> bool:
    """Copy a sample template into the project. Returns True on success."""
    template_path = _samples_dir() / template
    if not os.path.exists(template_path):
        available = available_templates()
        print(f"Template '{template}' not found. Available templates: {', '.join(available)}")
        return False

    for item in os.listdir(template_path):
        if _is_junk(item):
            continue
        src_item = os.path.join(template_path, item)
        dest_item = os.path.join(project_path, item)
        if os.path.isdir(src_item):
            shutil.copytree(
                src_item, dest_item,
                ignore=shutil.ignore_patterns(*_SKIP_NAMES, ".*"),
            )
        else:
            shutil.copy2(src_item, dest_item)
    print(f"Copied template '{template}' into the project.")
    return True


def _resolve_template(args, pick_template: bool) -> tuple[str | None, bool]:
    """Resolve the template name (or ``None`` for a blank project) before any
    directory is created.

    Returns ``(template, cancelled)``: ``cancelled`` is True when the user backed
    out of the interactive picker, so the caller creates nothing. Raises
    ``ValueError`` for an unknown ``--template`` — again before anything is
    written to disk.
    """
    template = args.template
    if not template and pick_template and sys.stdin.isatty():
        picked = _pick_template()
        if picked is None:
            return None, True
        template = picked or None
    if template and template not in available_templates():
        raise ValueError(
            f"Template '{template}' not found. "
            f"Available templates: {', '.join(available_templates())}")
    return template, False


def _init_git_repo(project_path: str, enabled: bool) -> None:
    """Best-effort ``git init`` inside the new project when requested."""
    if not enabled:
        return
    try:
        git_path = shutil.which("git")
        if git_path:
            subprocess.run([git_path, "init"], cwd=project_path,  # nosec B603
                           check=True, shell=False)
    except FileNotFoundError:
        print("Error: Git not found!")
    except subprocess.CalledProcessError as e:
        print(f"Error initializing git repository: {e}")


def create_project(args, *, show_next_steps: bool = True,
                   pick_template: bool = True):
    """
    Creates a new project structure for the Optics Framework.

    When no ``template`` was passed, stdin is a TTY and ``pick_template`` is
    True, an interactive picker built from ``samples/metadata.json`` offers the
    available samples plus a blank start. The template is resolved before any
    directory is touched, so backing out of the picker leaves no empty project
    folder behind.

    Parameters
    ----------
    args : argparse.Namespace
        The command-line arguments containing:
        - name (str): The name of the project (required).
        - path (str, optional): The directory where the project should be created.
        - force (bool, optional): If True, overrides an existing project directory.
        - template (str, optional): Name of a template to copy from `optics_framework/samples/`.
        - git_init (bool, optional): If True, initializes a Git repository in the project.
    show_next_steps : bool, keyword-only
        Print the "Next steps" guidance block at the end. Callers that print
        their own next steps afterwards (e.g. ``quickstart``) pass False to
        avoid duplicate — and for blank projects contradictory — advice.
    pick_template : bool, keyword-only
        Allow the interactive template picker to run when no template was
        given. Embedded callers (e.g. ``quickstart``, which asks its own
        template question first) pass False so ``template=None`` deterministically
        means "blank project" instead of re-prompting whenever stdin happens
        to be a TTY.

    Returns
    -------
    None

    Raises
    ------
    ValueError
        If the project directory already exists without ``force``, or the
        requested template is unknown. Neither case creates anything.
    """
    project_name = args.name
    base_path = args.path if args.path else os.getcwd()
    project_path = os.path.join(base_path, project_name)

    template, cancelled = _resolve_template(args, pick_template)
    if cancelled:
        print("Cancelled; no project created.")
        return

    _check_and_prepare_directory(project_path, args.force)

    if template:
        if not _copy_template(project_path, template):
            return
    else:
        _scaffold_project(project_path)

    _init_git_repo(project_path, args.git_init)

    print(f"\nProject ready at: {project_path}")
    # `configured=True` when a template was copied (a ready-to-tune config.yaml
    # exists); the scaffolded blank project still has the commented starter so
    # the user has work to do before running.
    if show_next_steps:
        print_next_steps(project_path, configured=bool(template))
        if not template:
            print("Test steps go in test_cases/test_cases.csv — run `optics list` "
                  "to see every available step.")

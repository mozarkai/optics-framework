import os
import shutil
import subprocess # nosec
import pathlib


# Files/directories that must never be copied out of a sample template.
_SKIP_NAMES = {"__pycache__"}


def _is_junk(name: str) -> bool:
    """True for hidden files (e.g. .DS_Store) and build cruft."""
    return name.startswith(".") or name in _SKIP_NAMES


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


def _check_and_prepare_directory(project_path: str, force: bool) -> bool:
    """Check if project directory exists and prepare it based on force flag."""
    if os.path.exists(project_path):
        if force:
            shutil.rmtree(project_path)
            print(
                f"Existing project folder removed due to --force: {project_path}")
        else:
            print(
                f"Project '{project_path}' already exists. Use --force to override.")
            return False
    os.makedirs(project_path)
    print(f"Created project directory: {project_path}")
    return True


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
    package_root = pathlib.Path(__file__).parent.parent
    template_path = package_root / "samples" / template
    if not os.path.exists(template_path):
        available = sorted(
            p.name for p in (package_root / "samples").iterdir()
            if p.is_dir() and not _is_junk(p.name)
        )
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


def create_project(args):
    """
    Creates a new project structure for the Optics Framework.

    Parameters
    ----------
    args : argparse.Namespace
        The command-line arguments containing:
        - name (str): The name of the project (required).
        - path (str, optional): The directory where the project should be created.
        - force (bool, optional): If True, overrides an existing project directory.
        - template (str, optional): Name of a template to copy from `optics_framework/samples/`.
        - git_init (bool, optional): If True, initializes a Git repository in the project.

    Returns
    -------
    None
    """
    project_name = args.name
    base_path = args.path if args.path else os.getcwd()
    project_path = os.path.join(base_path, project_name)

    if not _check_and_prepare_directory(project_path, args.force):
        return

    # A template is a complete, runnable sample — copy it verbatim. Otherwise
    # scaffold an empty starter project. (Previously we always scaffolded AND
    # then copied on top, producing a confusing hybrid layout.)
    if args.template:
        if not _copy_template(project_path, args.template):
            return
    else:
        _scaffold_project(project_path)

    if args.git_init:
        try:
            git_path = shutil.which("git")  # Get the absolute path of Git
            if git_path:
                subprocess.run([git_path, "init"], cwd=project_path,               #nosec B603
                            check=True, shell=False)
        except FileNotFoundError:
            print("Error: Git not found!")
        except subprocess.CalledProcessError as e:
            print(f"Error initializing git repository: {e}")

    print(f"\nProject ready at: {project_path}")
    if args.template:
        print(f"Next: review {project_name}/config.yaml, then run:  optics dry_run {project_path}")
    else:
        print(f"Next: enable a driver in {project_name}/config.yaml and add your "
              f"test cases, then run:  optics dry_run {project_path}")

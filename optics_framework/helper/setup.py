import re
import subprocess  # nosec B404
import threading
from collections import deque
from importlib.metadata import PackageNotFoundError, version
from typing import Deque, Dict, List, Optional
import sys
from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Checkbox, Button, Header, Footer, Static
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from pydantic import BaseModel


DISTRIBUTION_NAME = "optics-framework"
_STATUS_ID = "#status"


class SetupError(Exception):
    """A user-facing engine-setup error (bad version specifier, version
    conflict). Raised by `resolve_engines` and surfaced by the CLI as a clean
    message instead of a Python traceback."""


class EngineBackend(BaseModel):
    """A selectable engine backend, installed via an `optics-framework` extra.

    Covers every backend type the framework can load from ``optics_framework/
    engines/`` — action *drivers* (Appium/Selenium/…), OCR engines, and LLM
    engines. "Driver" is reserved for the action-driver subtype; this record is
    the generic install descriptor for any of them."""
    name: str          # human-friendly display name, e.g. "Google Vision"
    extra: str         # pyproject extra name, e.g. "google-vision"
    packages: List[str]  # concrete packages the extra pulls in (shown to the user)
    aliases: List[str] = []  # extra tokens accepted on the CLI


class EngineCategory(BaseModel):
    name: str
    engines: Dict[str, EngineBackend]


class InstallRequest(BaseModel):
    """One engine to install, optionally with a user-supplied version specifier.

    ``version`` is the raw PEP 508 tail (e.g. ``"==5.0.0"``, ``">=5.0,<5.4"``)
    parsed off the CLI token; ``None`` means "let the extra's own bound decide"."""
    engine: EngineBackend
    version: Optional[str] = None


# Engine-backend definitions. The `extra` matches the pyproject extra name, which
# in turn matches the config.yaml source key, so the word a user installs is the
# word they enable in config.
#
# ACTION_DRIVERS keeps the "driver" label deliberately: these implement
# DriverInterface and are configured under `driver_sources`. The OCR and LLM
# groups are engines, not drivers, and are named accordingly.
ACTION_DRIVERS = EngineCategory(
    name="Action Driver",
    engines={
        "Appium": EngineBackend(name="Appium", extra="appium", packages=["appium-python-client"]),
        "BLE": EngineBackend(name="BLE", extra="ble", packages=["pyserial"]),
        "Selenium": EngineBackend(name="Selenium", extra="selenium", packages=["selenium"]),
        "Playwright": EngineBackend(name="Playwright", extra="playwright", packages=["playwright"]),
    }
)

TEXT_ENGINES = EngineCategory(
    name="OCR Engine",
    engines={
        "EasyOCR": EngineBackend(name="EasyOCR", extra="easyocr", packages=["easyocr"]),
        "Pytesseract": EngineBackend(name="Pytesseract", extra="pytesseract", packages=["pytesseract", "pillow"]),
        "Google Vision": EngineBackend(
            name="Google Vision", extra="google-vision", packages=["google-cloud-vision"],
            aliases=["google_vision", "googlevision"],
        ),
    }
)

LLM_ENGINES = EngineCategory(
    name="LLM Engine",
    engines={
        "Gemini": EngineBackend(name="Gemini", extra="llm", packages=["google-genai"], aliases=["llm"]),
    }
)

ALL_ENGINES: Dict[str, EngineBackend] = {
    **ACTION_DRIVERS.engines, **TEXT_ENGINES.engines, **LLM_ENGINES.engines
}

# Convenience bundles — mirror the aggregate extras in pyproject.toml so a token
# like "all" or "web" resolves to the same set of engines the matching extra
# installs. Keys are already in normalised (`_norm`) form.
_BUNDLES: Dict[str, List[EngineBackend]] = {
    "mobile": [ACTION_DRIVERS.engines["Appium"]],
    "web": [ACTION_DRIVERS.engines["Selenium"], ACTION_DRIVERS.engines["Playwright"]],
    "vision": list(TEXT_ENGINES.engines.values()),
    "all": list(ALL_ENGINES.values()),
}

# Maps a checkbox-id prefix to its engine category.
_CATEGORY_BY_PREFIX = {"action": ACTION_DRIVERS, "text": TEXT_ENGINES, "llm": LLM_ENGINES}


def _norm(token: str) -> str:
    return token.strip().lower().replace(" ", "_").replace("-", "_")


# First char that starts a PEP 508 version specifier ( ==, >=, ~=, !=, <, > ).
_SPEC_START = re.compile(r"[=<>!~]")


def _split_token(token: str) -> tuple[str, Optional[str]]:
    """Split a CLI token into its engine name and optional version specifier.

    ``"appium==5.0.0"`` → ``("appium", "==5.0.0")``; ``"easyocr"`` → ``("easyocr",
    None)``. The specifier is only sliced off here — `_validate_spec` checks it —
    so any operator form is accepted, not just ``==``."""
    match = _SPEC_START.search(token)
    if match is None:
        return token.strip(), None
    return token[:match.start()].strip(), token[match.start():].strip()


def _validate_spec(spec: str, token: str) -> None:
    """Reject a malformed version specifier up front (PEP 440), so the user gets
    a clear error from optics instead of a pip failure later. ``"=5.0.0"`` (a
    single ``=``) and other typos raise `SetupError`."""
    try:
        SpecifierSet(spec)
    except InvalidSpecifier as exc:
        raise SetupError(
            f"invalid version in '{token}': {exc}. "
            "Use an operator like '==', e.g. 'appium==5.0.0'."
        ) from exc


def _alias_index() -> Dict[str, EngineBackend]:
    """Build a lookup from every accepted token (display name, extra, config key,
    explicit aliases) — all normalised to lowercase/underscore — to its
    EngineBackend."""
    index: Dict[str, EngineBackend] = {}
    for engine in ALL_ENGINES.values():
        for token in [engine.name, engine.extra, *engine.aliases]:
            index[_norm(token)] = engine
    return index


def _add_request(resolved: List[InstallRequest], engine: EngineBackend, version: Optional[str]) -> None:
    """Add `engine` to `resolved`, or merge `version` onto its existing entry.

    An explicit version wins over a bare one already recorded for that engine; a
    bare token folds into an existing entry. Two *different* explicit versions
    for one engine are a conflict — raise rather than silently pick one, matching
    how pip rejects `pkg==a pkg==b`."""
    existing = next((r for r in resolved if r.engine is engine), None)
    if existing is None:
        resolved.append(InstallRequest(engine=engine, version=version))
    elif version is None or version == existing.version:
        return
    elif existing.version is None:
        existing.version = version
    else:
        raise SetupError(
            f"conflicting versions for {engine.name}: "
            f"'{existing.version}' and '{version}'."
        )


def _resolve_token(
    token: str,
    index: Dict[str, EngineBackend],
    resolved: List[InstallRequest],
    invalid: List[str],
) -> None:
    """Resolve one CLI token, appending to `resolved` or `invalid` in place.

    A bundle token expands to its member engines (bare, no version); a
    specifier on a bundle token is rejected since one version can't apply to
    many packages. Anything else resolves through the alias index."""
    name, spec = _split_token(token)
    norm = _norm(name)
    bundle = _BUNDLES.get(norm)
    if bundle is not None:
        if spec is not None:
            invalid.append(token)
            return
        for engine in bundle:
            _add_request(resolved, engine, None)
        return
    engine = index.get(norm)
    if engine is None:
        invalid.append(token)
        return
    if spec is not None:
        _validate_spec(spec, token)
    _add_request(resolved, engine, spec)


def resolve_engines(tokens: List[str]) -> tuple[List[InstallRequest], List[str]]:
    """Resolve user-supplied tokens to InstallRequests. Returns (resolved, invalid).

    Accepts display names ("Appium", "Google Vision"), config/extra keys
    ("appium", "google-vision", "google_vision"), and convenience bundles
    ("mobile", "web", "vision", "all") — all case-insensitively. Any token may
    carry a PEP 508 version specifier ("appium==5.0.0", "easyocr>=1.7,<2.0") to
    override the extra's default bound; a specifier on a bundle is rejected as
    invalid since one version can't apply to many packages. Bundles expand to
    their member engines, deduplicated while preserving first-seen order; when
    the same engine is named twice, an explicit version wins over a bare one.

    Raises `SetupError` for a malformed version specifier or two conflicting
    explicit versions of the same engine."""
    index = _alias_index()
    resolved: List[InstallRequest] = []
    invalid: List[str] = []
    for token in tokens:
        _resolve_token(token, index, resolved, invalid)
    return resolved, invalid


class EngineInstallerApp(App):
    TITLE = "optics setup"

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("ctrl+c", "quit", "Quit"),
    ]

    CSS = """
    #engine-list {
        height: 1fr;
    }
    Checkbox {
        margin: 1;
    }
    Button {
        width: 20;
        margin: 1;
    }
    Static {
        padding: 1;
    }
    #status {
        color: $text-muted;
    }
    """

    def __init__(self):
        super().__init__()
        self.selected_engines: Dict[str, EngineBackend] = {}
        self._install_finished = False

    def compose(self) -> ComposeResult:
        yield Header()
        # Scroll the (potentially long) engine list so the action buttons below
        # stay on-screen even on a short terminal.
        with VerticalScroll(id="engine-list"):
            yield Static("Select engines to install:", classes="title")

            yield Static("Action Drivers:")
            for name, engine in ACTION_DRIVERS.engines.items():
                yield Checkbox(f"{name} ({', '.join(engine.packages)})", id=f"action_{name.lower().replace(' ', '_')}")

            yield Static("OCR Engines:")
            for name, engine in TEXT_ENGINES.engines.items():
                yield Checkbox(f"{name} ({', '.join(engine.packages)})", id=f"text_{name.lower().replace(' ', '_')}")

            yield Static("LLM Engines:")
            for name, engine in LLM_ENGINES.engines.items():
                yield Checkbox(f"{name} ({', '.join(engine.packages)})", id=f"llm_{name.lower().replace(' ', '_')}")

        yield Static("", id="status")
        yield Button("Install Selected", id="install", variant="primary")
        yield Button("Quit", id="quit", variant="error")
        yield Footer()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id is None:
            return
        category_prefix, engine_key = event.checkbox.id.split("_", 1)
        category = _CATEGORY_BY_PREFIX.get(category_prefix, ACTION_DRIVERS)
        engine_name = next(
            name for name in category.engines.keys()
            if name.lower().replace(' ', '_') == engine_key
        )
        engine = category.engines[engine_name]

        if event.checkbox.value:
            self.selected_engines[engine_name] = engine
        elif engine_name in self.selected_engines:
            del self.selected_engines[engine_name]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "install":
            self.install_engines()
        elif event.button.id == "quit":
            self.exit()

    def install_engines(self) -> None:
        """Kick off installation of every checked engine in a worker thread.

        The checkbox UI has no version field, so each selection installs at its
        extra's default bound. The pip install (and any Playwright browser
        download) runs off the UI thread so the TUI stays responsive instead of
        freezing for the duration."""
        if not self.selected_engines:
            self.notify("No engines selected!", severity="warning")
            return
        requests = [InstallRequest(engine=e) for e in self.selected_engines.values()]
        self._install_finished = False
        self.query_one("#install", Button).disabled = True
        self.query_one(_STATUS_ID, Static).update(
            "Installing… this can take a while (browser/model downloads); the UI stays responsive."
        )
        self._install_worker(requests)

    @work(thread=True)
    def _install_worker(self, requests: List[InstallRequest]) -> None:
        """Run the blocking install off the UI thread, then report back on it.

        A 1 Hz ticker thread keeps the status line showing elapsed time — the
        TUI's alternate screen would otherwise sit silent (pip output can't be
        printed here) for minutes during big downloads, which reads as a hang."""
        names = ", ".join(request.engine.name for request in requests)
        done = threading.Event()
        ticker = threading.Thread(
            target=self._tick_install_progress, args=(names, done), daemon=True)
        ticker.start()
        try:
            success, message = install_extras(requests, stream=False)
        finally:
            done.set()
            ticker.join()
        self.call_from_thread(self._on_install_finished, success, message)

    def _tick_install_progress(self, names: str, done: threading.Event) -> None:
        elapsed = 0
        while not done.wait(1.0):
            elapsed += 1
            self.call_from_thread(
                self._show_install_progress, f"Installing {names}… {elapsed}s")

    def _show_install_progress(self, text: str) -> None:
        # Checked on the UI thread so a tick already in flight when the install
        # finishes can never overwrite the final status.
        if self._install_finished:
            return
        self.query_one(_STATUS_ID, Static).update(text)

    def _on_install_finished(self, success: bool, message: str) -> None:
        self._install_finished = True
        status = message
        if success:
            # The TUI's alternate screen swallows print(), so fold the next
            # steps into the status line the user is already watching.
            status = f"{message}{install_next_steps_text()}"
        self.query_one(_STATUS_ID, Static).update(status)
        self.notify(
            message.splitlines()[0],
            severity="information" if success else "error",
            timeout=10,
        )
        # Re-enable Install so a failed run can be retried; harmless on success.
        self.query_one("#install", Button).disabled = False


def _installed_version() -> Optional[str]:
    try:
        return version(DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return None


def install_next_steps_text() -> str:
    """Concrete next steps shown after a successful engine install.

    Shared by the ``optics setup --install`` CLI path (printed) and the TUI's
    finish handler (folded into its status line) so both say the same thing."""
    return (
        "\nNext steps:\n"
        "  1. optics init myproject            # scaffold a new project\n"
        "  2. optics configure myproject       # write its config.yaml\n"
        "  3. optics doctor myproject          # verify the setup is ready\n"
        "  4. optics dry_run myproject         # validate without a device\n"
        "  5. optics execute myproject         # run on a real device"
    )


def print_install_next_steps() -> None:
    """Print the next-steps text for the ``optics setup --install`` CLI path."""
    print(install_next_steps_text())


# Last N output lines a streamed command keeps buffered, quoted on failure so a
# pip error excerpt survives without holding the whole (potentially huge) log.
_TAIL_LINES = 15


def _run_capture(cmd: List[str]) -> None:
    """Run cmd capturing all output (TUI path, where printing is swallowed).

    Raises CalledProcessError carrying e.stderr/e.stdout on failure."""
    subprocess.run(  # nosec B603
        cmd, capture_output=True, text=True, check=True, shell=False)


def _run_streaming(cmd: List[str]) -> None:
    """Run cmd echoing its merged output to the console live (CLI path), so long
    installs show pip's progress instead of minutes of silence.

    A short tail stays buffered and rides out on CalledProcessError.output if
    the command fails. Raises CalledProcessError on non-zero exit."""
    tail: Deque[str] = deque(maxlen=_TAIL_LINES)
    process = subprocess.Popen(  # nosec B603
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, shell=False)
    for line in process.stdout:
        if line.lstrip().startswith("Requirement already satisfied"):
            continue
        sys.stdout.write(line)
        sys.stdout.flush()
        tail.append(line.rstrip("\n"))
    process.stdout.close()
    if process.wait() != 0:
        raise subprocess.CalledProcessError(
            process.returncode, cmd, output="".join(f"{line}\n" for line in tail))


def install_extras(requests: List[InstallRequest], *, stream: bool = True) -> tuple[bool, str]:
    """Install the selected engine backends by pulling the matching
    `optics-framework` extras, pinned to the installed version so the CLI is
    never upgraded out from under the user.

    A request carrying an explicit ``version`` also adds its primary package as a
    concrete pinned requirement (e.g. ``appium-python-client==5.0.0``); pip
    intersects that with the extra's own bound, so an override outside the
    supported range fails loudly rather than silently downgrading it.
    ``packages[0]`` is each engine's primary package — the one a version
    override targets. Extra packages, like pytesseract's pillow, stay on the
    extra's bound.

    ``stream=True`` (the CLI default) echoes the command's output live so the
    user sees pip working; ``stream=False`` (the TUI, whose alternate screen
    swallows prints) captures quietly instead.

    Returns ``(success, message)`` rather than raising, so both the CLI
    (`optics setup --install`) and the TUI can surface the outcome — the TUI's
    alternate screen swallows ``print``. On failure the message carries an
    excerpt of the captured output. Callers decide how to display it."""
    if not requests:
        return False, "No engines selected."

    extras = sorted({req.engine.extra for req in requests})
    installed = _installed_version()
    spec = f"{DISTRIBUTION_NAME}[{','.join(extras)}]"
    if installed:
        spec = f"{spec}=={installed}"

    pinned = [f"{req.engine.packages[0]}{req.version}" for req in requests if req.version]

    run = _run_streaming if stream else _run_capture
    try:
        run([sys.executable, "-m", "pip", "install", spec, *pinned])

        if any(req.engine.extra == "playwright" for req in requests):
            # Per https://playwright.dev/python/docs/browsers this must run after
            # the pip install. Chromium is the most common target; --with-deps
            # pulls the required OS libraries.
            run([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"])

        return True, "Engines installed successfully!"
    except subprocess.CalledProcessError as e:
        # capture_output / _run_streaming route the command's diagnostics to
        # e.stderr/e.stdout rather than losing them, so fold them into the
        # message for a debuggable failure.
        detail = (e.stderr or "").strip() or (e.stdout or "").strip()
        message = f"Installation failed: {e}"
        if detail:
            message = f"{message}\n{detail}"
        return False, message


def list_engines() -> None:
    print("Available engines (install with `optics setup --install <name>`):\n")
    for category in (ACTION_DRIVERS, TEXT_ENGINES, LLM_ENGINES):
        print(f"{category.name}s:")
        for engine in category.engines.values():
            print(f"  {engine.extra:<15} ({', '.join(engine.packages)})")
        print()
    print("Bundles (install several at once):")
    for name, engines in _BUNDLES.items():
        print(f"  {name:<15} ({', '.join(engine.extra for engine in engines)})")
    print()
    print("Pin a version by appending a specifier, e.g. "
          "`optics setup --install appium==5.0.0`.")
    print()

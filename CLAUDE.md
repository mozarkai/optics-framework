# CLAUDE.md

Guidance for Claude Code (and other AI assistants) working in this repository. Keep it short and current — if a rule here stops being true, update it rather than working around it.

## Project in one paragraph

`optics-framework` is a no-code, vision-powered test automation framework distributed as a Python package and a CLI (`optics`). Test cases, modules, and elements are authored as CSV/YAML files; the CLI discovers them, builds a linked-list execution graph, and runs them against pluggable drivers (Appium / Selenium / Playwright / BLE) using pluggable element sources (page-source, screenshots, OCR, template matching). The runtime is async, with self-healing fallbacks, an event pub/sub layer, JUnit XML output, and a Rich live tree view.

User-facing docs are in `README.md` and `docs/`. Don't duplicate them here.

## Repo map (where things live)

- `optics_framework/api/` — keyword implementations exposed to test cases (`action_keyword.py`, `app_management.py`, `flow_control.py`, `verifier.py`).
- `optics_framework/common/` — core runtime: config, sessions, events, execution engine, error model, JUnit handler, models, factories, interfaces.
- `optics_framework/common/runner/` — CSV/YAML data readers, keyword registry, test runner, Rich printers.
- `optics_framework/engines/` — driver/element-source/vision-model implementations. Add new backends here behind the existing interfaces (`driver_interface.py`, `elementsource_interface.py`, `image_interface.py`, `text_interface.py`).
- `optics_framework/helper/` — CLI commands (`cli.py` dispatch, then `execute.py`, `initialize.py`, `setup.py`, `serve.py`, `generate.py`, `list_keyword.py`).
- `optics_framework/samples/` — bundled sample projects used by `optics init --template`.
- `tests/units/`, `tests/feature/` — pytest suites (see `pytest.ini` markers: `white_box`, `black_box`, `hybrid`, `generate`).
- `docs/` — mkdocs site. Architecture overview is in `docs/architecture.md`.

CLI entry point: `optics = "optics_framework.helper.cli:main"` (see `pyproject.toml`).

## Commands

Dependency management is Poetry. Common commands:

```bash
poetry install --with dev,test,docs   # full dev setup
poetry run pytest                     # run tests (uses --cov=optics_framework)
poetry run pytest tests/units/...     # target a subset
poetry run ruff check --fix .         # lint + autofix
poetry run pre-commit run --all-files # run the full hook suite
poetry run mkdocs serve               # docs preview
poetry build                          # build wheel/sdist
```

CLI smoke (after install):

```bash
optics setup --install Appium EasyOCR
optics init --name demo --path . --template contact
optics dry_run demo
optics execute demo
```

## Hard rules

1. **Run lint / pre-commit before committing.** At minimum run `poetry run ruff check --fix` on touched files; ideally `poetry run pre-commit run --files <changed>`. The hook chain (`ruff`, `bandit`, `gitleaks`, whitespace/yaml/json fixers, commitizen) gates the commit — fix the underlying issue rather than bypassing with `--no-verify`.

2. **Trace the source and blast radius of every change.** Before editing a function, locate where it is defined, every caller, and any subclass/override (`grep -r <name>` across `optics_framework/` and `tests/`). When changing public behavior of anything in `optics_framework/api/`, `common/` interfaces, or `engines/` base classes, walk the call graph far enough to know what breaks downstream — including sample projects under `optics_framework/samples/` and the public `optics.py` library surface. Report what you touched and what you verified.

3. **Conventional Commits.** This repo uses commitizen (`.cz.toml`), so commit messages must follow `feat:` / `fix:` / `refactor:` / `docs:` / `chore:` / `test:` etc. The commit-msg hook will reject otherwise.

4. **Python 3.12+ only.** `pyproject.toml` pins `python = ">=3.12,<4.0"`. Use modern typing (`list[str]`, `X | None`, `ParamSpec`, `Self`) — there's already a `typing_extensions` dep for anything not yet in the stdlib.

5. **Don't edit generated or vendored areas.** Leave `__pycache__/`, `docs/build/`, `dist/`, and `*.pyc` alone. Sample project assets under `optics_framework/samples/<template>/` are user-facing scaffolding — change them only when the templating contract itself changes, and update all templates together if you do.

## Conventions worth knowing

- **Async-first runtime.** `ExecutionEngine.execute()` runs under `asyncio.run()`. Keyword implementations and engine adapters should be `async def` where they do I/O.
- **Errors are coded.** `OpticsError` in `common/error.py` carries a `Code` (e.g. `E0201` = single element not found, `X0201` = all fallbacks exhausted). Use existing codes when raising; add new ones in `error.py` rather than scattering string messages.
- **Events drive reporting.** Anything observable (status changes, log capture) flows through `EventManager` in `common/events.py`; the JUnit handler and Rich printer are subscribers. Don't print directly to stdout in runtime code — emit an event.
- **Sessions own lifecycle.** `SessionManager.create_session()` / `terminate_session()` in `common/session_manager.py` handle driver init, JUnit cleanup, temp dir removal, and event-queue drain (configurable via `OPTICS_EVENT_DRAIN_TIMEOUT_S`, default 2s). Don't construct drivers or temp dirs outside this path.
- **Keyword lookup is normalized.** `KeywordRegistry` in `common/runner/keyword_register.py` matches snake_case-normalized names. New keywords go on the `api/` classes and are picked up automatically.
- **Fallbacks are a Cartesian product.** Element parameters can be lists; the runner tries combinations in order and only advances on `E0201`. Be aware when adding new element-source backends — they participate in this product.

## Watch out for

- Logging: use `internal_logger` from `common/logging_config.py`, not `logging.getLogger(...)` ad hoc — config and formatting are centralized.
- `optics_framework/optics.py` is the **library surface** (importable Python API distinct from the CLI). Changes there affect external consumers; treat its signatures as semi-public.
- Robot Framework integration is optional (`robot.api.deco` is imported with a fallback). Keep the fallback path working so the package installs without `robotframework`.
- `tests/conftest.py` may set up fixtures that mock engines — when adding driver-touching code, verify the matching fixture exists before assuming a test failure is real.

## When in doubt

Read the existing implementation in the relevant `optics_framework/<area>/` module before adding new abstractions — most extension points already exist behind an interface. Don't introduce a parallel mechanism without removing the old one.

# CLAUDE.md

Concrete map of the optics-framework runtime for Claude Code and similar tools. All line numbers below are anchors at the time of writing — if a `path:line` no longer matches the named symbol, fix this file instead of trusting it.

## Execute journey (CLI → keyword call)

The chain that runs when a user types `optics execute <folder>`:

1. `optics_framework/helper/cli.py:321` — `main()` builds the argparse subparsers and dispatches. `ExecuteCommand.execute` (`cli.py:278`) calls `execute_main(folder_path, runner, use_printer)` in `optics_framework/helper/execute.py:655`.
2. `execute_main` constructs `ExecuteRunner(args)` (`execute.py:643`, subclass of `BaseRunner` at `execute.py:485`) and wraps `asyncio.run(...)`.
3. `BaseRunner.__init__` (`execute.py:488`) runs the discovery + loading pipeline synchronously:
   - `find_files(folder_path)` (`execute.py:53`) walks the dir, sniffs each CSV header / YAML top-level keys via `identify_file_content` (`execute.py:203`) and `_categorize_file_by_content` (`execute.py:137`), and routes paths to `test_case` / `module` / `element` / `api` buckets. A YAML that has both `driver_sources` and `element[s]_sources` is recognised as the project config (`_is_config_file`, `execute.py:118`).
   - `_load_test_cases` / `_load_modules` / `_load_elements` / `_load_api_data` (`execute.py:531`–`585`) read each file with `CSVDataReader` or `YAMLDataReader` from `common/runner/data_reader.py:105` / `:207` and populate `ModuleData` / `ElementData` / `ApiData` (defined in `common/models.py:139`, `:154`, `:278`).
   - `_load_templates` → `discover_templates` (`execute.py:30`) collects every `.png/.jpg/...` under the project into a `TemplateData` (`models.py:293`).
   - `_filter_and_build_execution_queue` calls `filter_test_cases` (`execute.py:279`, honours `include`/`exclude` in config, always keeps suite-setup/teardown) and `build_linked_list` (`execute.py:433`) which threads `TestCaseNode → ModuleNode → KeywordNode` (`models.py:69` / `:34` / `:28`) using the per-test-case ordering from `get_execution_queue` (`execute.py:349`).
   - `_setup_session` calls `SessionManager.create_session` (`common/session_manager.py:149`) which builds a `Session` (`session_manager.py:99`): instantiates `EventSDK`, `OpticsBuilder` (`common/optics_builder.py:28`), and — via `_get_enabled_config_list` (`session_manager.py:29`) — only the dependencies whose `DependencyConfig.enabled == True` from `config.yaml`. `Session.__init__` immediately wires `add_driver` / `add_element_source` / `add_text_detection` / `add_image_detection` and calls `self.optics.get_driver()` to fail fast if drivers can't initialise.
4. `BaseRunner.run("batch")` (`execute.py:616`) constructs `ExecutionParams` and awaits `ExecutionEngine.execute` (`common/execution.py:365`).
5. `ExecutionEngine.execute`:
   - Pulls the session-scoped `EventManager` from `get_event_manager(session_id)` (`common/events.py:206`, registry at `events.py:174`) and `.start()`s its async dispatch loop.
   - `RunnerFactory.create_runner` (`execution.py:211`) instantiates a new `KeywordRegistry` (`common/runner/keyword_register.py:5`) and registers `ActionKeyword`, `AppManagement`, `Verifier`, `FlowControl` — `KeywordRegistry.register` (`keyword_register.py:22`) walks `dir(instance)` and maps every non-underscore callable into `keyword_map`. Picks `TestRunner` (`runner/test_runnner.py:89`), `PytestRunner` (`:758`), or `KeywordRunner` (`:1213`) based on `runner_type`.
   - Wraps the body in `LoggerContext(session_id)` (`common/logging_config.py`) so logs carry the session.
   - `BatchExecutor.execute` (`execution.py:50`) → `TestRunner.run_all` (`test_runnner.py:728`).
6. `TestRunner.run_all` walks the `TestCaseNode` chain → `_process_test_case` (`:538`) → `_process_module` (`:478`) → `_execute_keyword` (`:310`). The latter:
   - Resolves `func_name = "_".join(name.split()).lower()` and looks it up in `keyword_map`.
   - `_build_param_candidates` (`:378`) expands every `${var}` to `ElementData.get_element(var)` — a list — and other params to single-element lists.
   - `_try_execute_with_fallback` (`:410`) iterates `itertools.product(*param_candidates)` (capped at `MAX_ATTEMPTS = 20`, `:414`). On each combination it calls the bound method. The fallback ladder advances **only** when the keyword raises `OpticsError` whose `code` starts with `E02` (element-not-found family) or equals `Code.X0201` (`:437`); any other exception is fatal and dispatched to `_handle_keyword_exception` (`:448`).
   - Every state transition publishes an `Event` (`common/events.py:33`) via `_send_event` (`:250`); subscribers (`JUnitEventHandler` in `common/Junit_eventhandler.py:110`, `TreeResultPrinter` in `common/runner/printers.py`) consume them off the per-session queue.
7. On the way out, `BaseRunner.cleanup` (`execute.py:635`) → `SessionManager.terminate_session` (`session_manager.py:164`) terminates the driver, clears `inline_templates`, `shutil.rmtree`s the per-session temp dir, and removes the session's `EventManager`. `ExecutionEngine._drain_events_and_shutdown` (`execution.py:347`) waits up to `OPTICS_EVENT_DRAIN_TIMEOUT_S` (env, default `2.0s`) for the queue to empty before shutting the manager.

`dry_run` follows the same path but with `DryRunExecutor` (`execution.py:92`) and `TestRunner._process_test_case(..., dry_run=True)` — it resolves params and checks `keyword_map` membership without invoking methods.

## Where to put things

- **A new keyword:** add an `async def` or `def` method on the relevant class in `optics_framework/api/` (`action_keyword.py:146` `ActionKeyword`, `app_management.py:7` `AppManagement`, `verifier.py:11` `Verifier`, `flow_control.py:40` `FlowControl`). `KeywordRegistry.register` (`keyword_register.py:22`) picks up every public callable automatically; the runtime name is `" ".join(method_name.split("_")).title()`-ish — the lookup is the inverse (`"_".join(name.split()).lower()`, `test_runnner.py:340`).
- **A new driver backend:** add a module under `optics_framework/engines/drivers/` (alongside `appium.py`, `selenium.py`, `playwright.py`, `ble.py`) that subclasses `DriverInterface` (`common/driver_interface.py`). `DeviceFactory` (`common/factories.py:10`, default package `optics_framework.engines.drivers`) discovers it dynamically via `GenericFactory.create_instance_dynamic` (`common/base_factory.py:14`). Set `NAME` class attribute if any element source needs to match against it (`factories.py:60`).
- **A new element source:** add to `optics_framework/engines/elementsources/` implementing `ElementSourceInterface` (`common/elementsource_interface.py`). Set `REQUIRED_DRIVER_TYPE = "appium"` (or similar) on the class if the source needs a driver injected; `ElementSourceFactory._find_matching_driver` (`factories.py:60`) wires the matching driver instance into `__init__` as `driver=...`.
- **A new OCR / image detector:** drop into `optics_framework/engines/vision_models/ocr_models/` or `…/image_models/` and implement `TextInterface` / `ImageInterface` (`common/text_interface.py`, `common/image_interface.py`). Picked up by `TextFactory` / `ImageFactory` (`factories.py:81`, `:90`).
- **A new CLI subcommand:** subclass `Command` in `helper/cli.py:16`, add to the `commands` list at `cli.py:341`. Per-command Pydantic args models live next to each command (e.g. `ExecuteArgs` at `cli.py:242`).
- **A new error code:** add to the `Code` enum (`common/error.py:34`) and an `ErrorSpec` entry in `ERROR_REGISTRY` (`error.py:107`). To trigger fallback semantics, use the `E02xx` prefix (matched at `test_runnner.py:437`).
- **A new event subscriber (custom reporter):** subclass `EventSubscriber` (`events.py:64`), subscribe via `EventManager.subscribe(subscriber_id, instance)` (`events.py:137`). Implement `close()` if you hold file handles — `EventManager.shutdown` (`events.py:158`) calls it.
- **A new config field:** extend `Config` in `common/config_handler.py:22`; defaults backfilled in `Config.__init__` (`config_handler.py:42`); `deep_merge` (`:82`) handles project-config-over-global-config layering in `ConfigHandler.load` (`:142`).
- **A new project sample / template:** add a directory under `optics_framework/samples/` (siblings of `contact/`, `youtube/`, `calendar/`, `clock/`, `gmail_web/`, `playwright/`) with `config.yaml`, `test_cases/*.csv`, `modules/*.csv`, `test_data/`. The `init` command surfaces it via `optics init --template <dirname>`.

## Key data structures

- **Linked-list execution graph** — `TestCaseNode` (`models.py:69`) → `ModuleNode` (`:34`) → `KeywordNode` (`:28`); all carry a `state: State` enum (`:9`) that mirrors `EventStatus` (`events.py:13`). Always traverse with `current = head; while current: ...; current = current.next` — there are no helper iterators.
- **`ElementData.elements: Dict[str, List[str]]`** (`models.py:154`) — each element name maps to an ordered fallback list. `get_element(name)` returns the list; `get_first(name)` returns the first value. The runner uses the list as one axis of the Cartesian product.
- **`OpticsError` codes** (`error.py:34`, severities `E` / `W` / `X`):
  - `E0201` element not found in one source → triggers fallback in the runner.
  - `X0201` element not found after all fallbacks → also continues fallback ladder.
  - `E0402` keyword name not in `keyword_map`.
  - `E0501` config / required-files missing.
  - `E0701` execution failed (top-level wrap).
  - `E0702` test case / session missing.
- **`Event`** (`events.py:33`) carries `entity_type ∈ {"execution","test_case","module","keyword","session"}`, `entity_id`, `parent_id`, `status: EventStatus`, `args`, `start_time`/`end_time`/`elapsed`, `logs` (captured via `LogCaptureBuffer`, `Junit_eventhandler.py:93`).

## Engine wiring (how a `Session` becomes a driver)

`Session.__init__` (`session_manager.py:99`) builds an `OpticsBuilder` (`optics_builder.py:28`), then for each of `driver_sources` / `elements_sources` / `text_detection` / `image_detection` calls the matching `add_*` to stash config. `OpticsBuilder.get_driver/.get_element_source/...` (`:150`–`:168`) lazy-instantiate by delegating to the factory: e.g. `DeviceFactory.get_driver` → `GenericFactory.create_instance_dynamic` (`base_factory.py:14`) which imports `optics_framework.engines.drivers.<name>` and calls its `__init__` with the config dict (plus `event_sdk` for drivers, plus matched `driver=` for element sources).

Multiple enabled entries become an `InstanceFallback` (`base_factory.py:207`) — call sites iterate `.instances` to try each in order. This is the priority chain referenced in the README "self-healing" claim.

API classes (`ActionKeyword`, etc.) receive the builder in `__init__` and call `self.optics.get_element_source()` / `get_driver()` / `get_text_detection()` lazily, so engines aren't instantiated until the first keyword needs them.

## Output artefacts

For an `execute` run, `config.execution_output_path` (default `<project>/execution_output/`, ensured in `config_handler.py:120`) receives:
- `junit_output.xml` — written incrementally by `JUnitEventHandler` (`Junit_eventhandler.py:110`), flushed in `flush()` (`:253`) and finalised in `close()` (`:268`).
- `logs.json` — if `config.json_log: true`, path set by `_maybe_setup_junit` (`session_manager.py:40`).
- screenshots, when keywords call `_capture_screenshot_safe` / `_save_screenshot_if_available` (`action_keyword.py:169` / `:177`).

## Hard rules

1. **Run pre-commit before committing.** `.pre-commit-config.yaml` chains ruff (with `--fix`), bandit (excluding `tests/`), trailing-whitespace, end-of-file-fixer, check-yaml, check-json, commitizen (commit-msg), gitleaks. Either run `poetry run pre-commit run --files <changed>` or let the git hook fire — never `--no-verify`.
2. **Trace the source and blast radius before changing a function.** Run `grep -rn "<name>" optics_framework/ tests/ optics_framework/samples/` for every symbol you touch. Pay attention to: (a) the `keyword_map` lookup, since renaming a method on `ActionKeyword`/`AppManagement`/`Verifier`/`FlowControl` silently breaks every CSV/YAML that names it; (b) `Code.<E…>` consumers — the `E02*` / `X0201` prefix in `test_runnner.py:437` is load-bearing for fallback semantics; (c) `Event.entity_type` values, since `JUnitEventHandler._handle_*_event` dispatches on them.
3. **Conventional Commits.** `.cz.toml` configures commitizen; the commit-msg hook will reject anything that isn't `feat:` / `fix:` / `refactor:` / `docs:` / `chore:` / `test:` / `perf:` / `style:` / `build:` / `ci:`.
4. **Python 3.12+ only.** `pyproject.toml:25` pins `python = ">=3.12,<4.0"`. Modern typing (`list[str]`, `X | None`, `ParamSpec`, `Self`) is fine; `typing_extensions` is available for newer features.
5. **Do not edit generated trees.** `__pycache__/`, `dist/`, `docs/build/`, `.tox/`, `htmlcov/`, `execution_output/` are runtime artefacts.

## Pitfalls specific to this codebase

- **Async re-entrancy.** `ExecutionEngine.execute` and everything below it must run inside `asyncio.run(...)` — `BaseRunner.run` is the only sanctioned entry. `PytestRunner` (`test_runnner.py:758`) sidesteps the async loop by using `queue_event_sync` (`:79`) which spins up `asyncio.run` per event; do not mix the two in the same flow.
- **`KeywordRegistry.register` is greedy.** It registers every public callable on the instance — adding a public helper to `ActionKeyword` instantly exposes it as a keyword and risks colliding with another class's method (it only logs a warning on collision, `keyword_register.py:36`). Prefix helpers with `_`.
- **Element ref `${name}` resolution happens twice.** `TestRunner.resolve_param` (`test_runnner.py:187`) returns the *first* value; `_build_param_candidates` (`:378`) returns the *list* for fallback. Dry-run uses the first form, real execution uses the list form — diverging behaviour in dry-run vs execute usually traces here.
- **`Session` mutates the passed-in `Config`.** `_maybe_setup_junit` (`session_manager.py:40`) sets `config.json_path`, and `ConfigHandler.__init__` (`config_handler.py:118`) sets `execution_output_path` and creates the directory. Don't pass a shared `Config` to two sessions.
- **`InstanceFallback`** (`base_factory.py:207`) is the priority list referenced as "self-healing". Iterate `.instances`, don't index — order is the priority order from `config.yaml`.
- **`OPTICS_EVENT_DRAIN_TIMEOUT_S`** (`execution.py:266`, default `2.0`) caps how long shutdown waits for events to flush. If JUnit XML looks truncated for long runs, raise this env before suspecting a logic bug.
- **Robot Framework is optional.** `optics_framework/optics.py:50` falls back to a no-op `keyword`/`library` decorator if `robotframework` isn't installed. Don't add a hard import of `robot.api` anywhere; mirror the try/except.
- **Two test trees.** `tests/units/` and `tests/feature/` both run under the same `pytest` invocation (`pyproject.toml:73` `testpaths = ["tests"]`). The `pytest.ini` markers (`white_box`, `black_box`, `hybrid`, `generate`) let you scope: `pytest -m white_box`. `tests/conftest.py` injects shared fixtures — read it before assuming a failure is real.

## Commands

```bash
poetry install --with dev,test,docs       # full setup
poetry run pytest                          # all tests + coverage (configured in pyproject.toml)
poetry run pytest -m white_box             # unit subset
poetry run ruff check --fix .              # lint + autofix
poetry run pre-commit run --files <paths>  # full hook chain on touched files
poetry run mkdocs serve                    # docs preview on :8000
poetry build                               # wheel + sdist
optics execute <folder>                    # smoke a project against the installed CLI
```

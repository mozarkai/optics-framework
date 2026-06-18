# CLAUDE.md

Concrete map of the optics-framework runtime for Claude Code and similar tools. All line numbers below are anchors at the time of writing — if a `path:line` no longer matches the named symbol, fix this file instead of trusting it.

## Execute journey (CLI → driver action)

The full chain when a user types `optics execute <folder>`:

1. `optics_framework/helper/cli.py:321` — `main()` builds argparse subparsers, dispatches via `ExecuteCommand.execute` (`cli.py:278`) → `execute_main(folder_path, runner, use_printer)` in `optics_framework/helper/execute.py:728`.
2. `execute_main` constructs `ExecuteRunner(args)` (`execute.py:716`, subclass of `BaseRunner` at `execute.py:600`) and wraps `asyncio.run(...)`.
3. `BaseRunner.__init__` (`execute.py:603`) runs discovery + loading synchronously:
   - `find_files(folder_path)` (`execute.py:53`) walks the dir, sniffs CSV headers / YAML top-level keys via `identify_file_content` (`execute.py:203`) and `_categorize_file_by_content` (`execute.py:137`), routes paths to `test_case` / `module` / `element` / `api` buckets. A YAML with both `driver_sources` and `element[s]_sources` is recognised as project config (`_is_config_file`, `execute.py:118`).
   - `_load_test_cases` / `_load_modules` / `_load_elements` / `_load_api_data` (`execute.py:641`–`657`) delegate to the module-level loaders `load_test_cases_data` / `load_modules_data` / `load_elements_data` / `load_api_data_files` (`execute.py:462`–`520`), which read each file via `CSVDataReader` / `YAMLDataReader` (`common/runner/data_reader.py:105` / `:207`) and populate `ModuleData` / `ElementData` / `ApiData` (`common/models.py:139`, `:154`, `:278`). `load_suite_from_folder` (`execute.py:535`) composes these into a `LoadedSuite` (`execute.py:522`) without creating a session — reused by the REST dry-run endpoint.
   - `_load_templates` → `discover_templates` (`execute.py:30`) collects every `.png/.jpg/...` into `TemplateData` (`models.py:293`).
   - `_filter_and_build_execution_queue` → `filter_test_cases` (`execute.py:279`, honours `include`/`exclude`, always keeps setup/teardown) + `build_linked_list` (`execute.py:433`) which threads `TestCaseNode → ModuleNode → KeywordNode` (`models.py:69` / `:34` / `:28`).
   - `_setup_session` → `SessionManager.create_session` (`common/session_manager.py:149`) which builds a `Session` (`session_manager.py:99`): instantiates `EventSDK`, `OpticsBuilder` (`common/optics_builder.py:28`), and via `_get_enabled_config_list` (`session_manager.py:29`) keeps only `DependencyConfig.enabled == True` entries. `Session.__init__` calls `add_driver` / `add_element_source` / `add_text_detection` / `add_image_detection` then `self.optics.get_driver()` to fail fast.
4. `BaseRunner.run("batch")` (`execute.py:689`) constructs `ExecutionParams` and awaits `ExecutionEngine.execute` (`common/execution.py:365`).
5. `ExecutionEngine.execute`:
   - Pulls session-scoped `EventManager` from `get_event_manager(session_id)` (`common/events.py:206`, registry at `:174`) and `.start()`s its dispatch loop.
   - `RunnerFactory.create_runner` (`execution.py:211`) constructs `KeywordRegistry` (`common/runner/keyword_register.py:5`), then via `session.optics.build(cls)` (`optics_builder.py:170`) instantiates `ActionKeyword` (`api/action_keyword.py:146`), `AppManagement` (`api/app_management.py:7`), `Verifier` (`api/verifier.py:11`), `FlowControl` (`api/flow_control.py:40`), and registers each. `KeywordRegistry.register` (`keyword_register.py:22`) walks `dir(instance)` and maps every non-underscore callable into `keyword_map`. Then picks `TestRunner` (`runner/test_runnner.py:89`), `PytestRunner` (`:758`), or `KeywordRunner` (`:1213`) based on `runner_type`.
   - `BatchExecutor.execute` (`execution.py:50`) → `TestRunner.run_all` (`test_runnner.py:728`).
6. `TestRunner.run_all` walks the `TestCaseNode` chain → `_process_test_case` (`:538`) → `_process_module` (`:478`) → `_execute_keyword` (`:310`):
   - Resolves `func_name = "_".join(name.split()).lower()` and looks it up in `keyword_map`.
   - `_build_param_candidates` (`:378`) expands every `${var}` to `ElementData.get_element(var)` — the **fallback list** — and other params to single-element lists.
   - `_try_execute_with_fallback` (`:410`) iterates `itertools.product(*param_candidates)` (capped at `MAX_ATTEMPTS = 20`, `:414`). On each combination it calls the bound method. The ladder advances **only** when the raised `OpticsError.code` starts with `E02` (element-not-found family) or equals `Code.X0201` (`:437`); any other exception is fatal via `_handle_keyword_exception` (`:448`). This is **fallback level 1** (param-axis).
7. The bound method runs — for an `ActionKeyword` method decorated with `@with_self_healing` (`api/action_keyword.py:120`, applied to every locate-based action: `press_element` `:183`, `detect_and_press` `:264`, `press_checkbox` `:281`, `swipe_until_element_appears` `:370`, `scroll_until_element_appears` `:426`, `scroll_from_element` `:451`, `enter_text` `:516`, `enter_number` `:561`):
   - `wrapper` (`:122`) captures a screenshot via `self._capture_screenshot_safe()` → `StrategyManager.capture_screenshot` (`common/strategies.py:721`).
   - `_parse_aoi_from_kwargs` (`:20`) pulls `aoi_x/y/width/height` and `index` out of kwargs.
   - `_locate_element` (`:40`) calls `strategy_manager.locate(element, ...)` which is a **generator** (`strategies.py:606`).
   - `_try_results_until_success` (`:86`) consumes the generator: for each `LocateResult` yielded, saves an annotated screenshot then calls the wrapped function with `located=result.value`. First success returns; if every yielded result raises, it wraps the last as `OpticsError(Code.X0201, ...)` (`:111`); if the generator yielded nothing the strategies layer already raised `Code.E0201` (`strategies.py:624`).
8. `StrategyManager.locate` (`strategies.py:606`) is the **self-healing element location** layer (**fallback level 2**):
   - `utils.parse_text_only_prefix(element)` (`common/utils.py:173`) strips the optional `TEXT_ONLY:` prefix that forces vision-based search.
   - `utils.determine_element_type(element)` (`utils.py:135`) classifies the locator string: `.png/.jpg/.jpeg/.bmp` → `"Image"`; `text=` / `TEXT_ONLY:` → `"Text"`; `css=` → `"CSS"`; `xpath=`, `/`, `//`, `(` → `"XPath"`; else `"Text"`.
   - Iterates `self.locator_strategies` — built by `StrategyFactory` (`strategies.py:469`) from a priority-ordered registry (`:475`):
     1. `XPathStrategy` (`:133`) — delegates to `element_source.locate(element)` (e.g. Appium's `find_element` by XPath).
     2. `TextElementStrategy` (`:157`) — same `element_source.locate` call but for `"Text"` / `"CSS"` / `"Class"` types.
     3. `TextDetectionStrategy` (`:181`) — captures a screenshot via the element source and runs OCR through `self.text_detection.find_element` (the `TextInterface` instance configured in `text_detection` block of config.yaml).
     4. `ImageDetectionStrategy` (`:302`) — same flow but uses `image_detection.find_element` (template matching or remote OIR).
   - Each strategy's static `supports(element_type, element_source)` (`:54`) gates whether it runs. `LocatorStrategy._is_method_implemented` (`:65`) parses the method source to skip stubs that just `raise NotImplementedError`.
   - For each strategy `_try_strategy_locate` (`:570`) calls `locate(...)` (or `locate_with_aoi(...)` when AOI params are non-default) and `execution_tracer.log_attempt(...)` records the outcome (`common/execution_tracer.py`). Successful results are yielded as `LocateResult(value, strategy, annotated_frame)` (`:507`). If the loop yields nothing, raises `OpticsError(Code.E0201, ...)` (`:624`).
9. The keyword body (e.g. `press_element` at `action_keyword.py:202`) consumes the located result: if it's a `(x, y)` tuple it calls `self.driver.press_coordinates(...)`; otherwise `self.driver.press_element(located_node, repeat, event_name)`. `self.driver` is an `InstanceFallback[DriverInterface]` (`common/base_factory.py:207`) — **fallback level 3** (driver-axis) — call sites iterate `.instances` to try each enabled driver in `config.yaml` priority order.
10. Throughout, `TestRunner._send_event` (`test_runnner.py:250`) publishes `Event`s (`events.py:33`) to the per-session `EventManager`. Subscribers: `JUnitEventHandler` (`common/Junit_eventhandler.py:110`) builds the XML tree, `TreeResultPrinter` (`common/runner/printers.py`) updates the Rich live display, both off the same async queue.
11. On the way out, `BaseRunner.cleanup` (`execute.py:708`) → `SessionManager.terminate_session` (`session_manager.py:164`) terminates the driver, clears `inline_templates`, `shutil.rmtree`s the per-session temp dir, removes the session's `EventManager`. `ExecutionEngine._drain_events_and_shutdown` (`execution.py:347`) waits up to `OPTICS_EVENT_DRAIN_TIMEOUT_S` (env, default `2.0s`) for the queue to empty.

`dry_run` follows the same path with `DryRunExecutor` (`execution.py:92`) and `TestRunner._process_test_case(..., dry_run=True)` — it resolves params via `resolve_param` (`test_runnner.py:187`, returns *first* element value, not the list) and checks `keyword_map` membership without calling methods. The first-vs-list resolution divergence is a known dry-run vs execute pitfall. `DryRunExecutor.execute` returns the per-test-case `result_printer.test_state` (`Dict[str, TestCaseResult]`); the CLI ignores it, but the REST dry-run endpoint surfaces it (see HTTP entry point below). A dry run only needs a *configured* (not connected) driver — drivers instantiate lazily (`self.driver = None`), so it never touches the device.

## Live session journey (`optics live`)

`optics live` opens an interactive session that runs keywords against an already-running target without a test-case folder. It is a **separate driver path from execute** (no `ExecutionEngine`, no `EventManager`, no JUnit), but it deliberately reuses the runner's keyword + element machinery rather than reimplementing it.

1. `cli.py:298` `LiveCommand` → `live_main(folder_path)` (`helper/live.py:1164`). `LiveArgs` (`cli.py:293`) is the Pydantic args model.
2. `_compose_config` (`live.py:185`) builds a `Config` from the project folder (`_config_from_yaml` / `_load_partial_config`, `:128`/`:159`) — **config-driven and driver-agnostic**: whichever single driver is enabled in `config.yaml` wins (`_enabled_drivers`, `:175`). There is no hard-coded driver choice.
3. `LiveController.__init__` (`live.py:223`) creates a `Session` via `SessionManager` (same `Session` as execute) and calls `_build_registry` (`:354`), which builds a `KeywordRegistry` from `session.optics.build(ActionKeyword/AppManagement/Verifier)` — identical to `RunnerFactory.create_runner`. So **live executes through the API layer (`ActionKeyword` etc.), not the engines directly**, and inherits self-healing, AOI, and screenshot handling for free.
4. Keyword input → `run_keyword` (`live.py:450`) → `_execute_line` (`:460`). Live **reimplements the param-fallback ladder (level 1)** in `_attempt_combos` / `_build_candidates` / `_resolve_candidate` (`:566`/`:605`/`:619`) — this mirrors `TestRunner._try_execute_with_fallback` / `_build_param_candidates` (the runner's logic isn't factored out for reuse, so the two must be kept in sync). `${var}` resolution reads the same `ElementData`.
5. **Natural-language mode** — `NaturalLanguageAgent` (`common/nl_agent.py:134`) is a bounded ReAct loop: screenshot → `LLMInterface.generate_json(prompt, ACTION_SCHEMA, images=[png])` → validated action → execute one keyword from the same `keyword_map` → repeat (`max_steps=15`). Wired in `LiveController._get_nl_agent` (`live.py:924`) and driven by `run_natural_language` (`:943`); recording is commit-on-done so a failed instruction doesn't pollute `/save`. The LLM comes from `session.optics.get_llm()` (the `llm_models` engine, default `gemini`, disabled unless configured — install extra `optics-framework[llm]`).
6. The TUI lives in `helper/live_tui.py`; `/save` serialises recorded steps to CSV (`save`, `live.py:686`). User docs: `docs/usage/live_usage.md`.

**Device coupling caveat (reviewer note).** Live's *driver* selection is fully config-driven, but its optional **device** features are not driver-agnostic: `list_devices` (`live.py:810`) shells out to `adb devices` and `switch_device` (`:829`) rebuilds the session with new `udid`/`deviceName` caps — Android/Appium only, gated by `supports_device_switching` (`:797`). This is device-management knowledge (adb) leaking into the live helper rather than sitting behind `DriverInterface`; the Appium driver itself already owns the analogous device/install concerns (`adb`/`ideviceinstaller` in `engines/drivers/appium.py`). If device hot-swap should generalise, it belongs behind the driver interface, not in `helper/live.py`.

## Element location pipeline (deep dive)

The element-location subsystem is spread across four layers; all of them live in `common/strategies.py` and have hooks elsewhere.

- **`StrategyManager`** (`strategies.py:522`) — constructed once per `ActionKeyword` (`action_keyword.py:164`) and per `Verifier` (`verifier.py:21`). It owns three strategy lists:
  - `locator_strategies` (built by `StrategyFactory`, `:469`) — for finding a single element.
  - `screenshot_strategies` (`ScreenshotFactory`, `:499`) — `capture()` → `np.ndarray`, rejects black screens (`:452`).
  - `pagesource_strategies` (`PagesourceFactory`, `:491`) — `get_page_source()` → `(xml_source, timestamp)`.
  Built by iterating `self.element_source.instances` (the `InstanceFallback`) and calling each factory's `create_strategies(instance)`. Enabling multiple element sources in `config.yaml` multiplies the strategy list.
- **`StrategyFactory.create_strategies`** (`:482`) filters the registry by `cls.supports(element_type, source)` then sorts by priority — XPath(1) < TextElement(2) < TextDetection(3) < ImageDetection(4). Lower priority wins for a given element string.
- **`StrategyManager.locate(...)`** (`:606`) yields `LocateResult` per successful strategy (see journey step 8). Used by `@with_self_healing`.
- **`StrategyManager.assert_presence(elements, element_type, timeout, rule)`** (`:644`) is the parallel path used by `Verifier.assert_presence` (`api/verifier.py:74`). It allocates the total `timeout` across applicable strategies via `_alloc_time_for_strategy` (`:626`, even division of remaining time) and short-circuits on the first one that satisfies the rule (`any` / `all`).
- **`LocateValueWithFrame`** (`:20`) is the vision-strategy return shape: `(value, annotated_frame)`. The `LocateResult` wrapper (`:507`) normalises both shapes for callers; `annotated_frame` ends up in screenshots saved by `_save_annotated_for_result` (`action_keyword.py:52`).
- **AOI variants** — `TextDetectionStrategy.locate_with_aoi` (`:211`) and `ImageDetectionStrategy.locate_with_aoi` (`:328`) crop the screenshot to an Area-of-Interest first via `utils.crop_screenshot_to_aoi`, then adjust coordinates back via `utils.adjust_coordinates_for_aoi`. Only triggered when any of `aoi_x/y/width/height` differs from the `0/0/100/100` default (`action_keyword.py:26`).
- **`execution_tracer`** (`common/execution_tracer.py`, `ExecutionTracer` class) is called from `_try_strategy_locate` (`strategies.py:599`/`:602`) and `_try_assert_with_strategy` (`:709`/`:712`/`:717`) to record success/fail per strategy — read it to build the per-keyword "which strategy actually worked" report.

### Three fallback ladders — keep them straight

| Level | Where | Iterates over | Trigger to advance | When exhausted |
|------|-------|---------------|---------------------|----------------|
| 1 — param | `TestRunner._try_execute_with_fallback` (`test_runnner.py:410`) | Cartesian product of `${name}` value lists from `ElementData` | `OpticsError` with code starting `E02` or `== X0201` | `_handle_fallback_exhausted` (`:463`) |
| 2 — strategy | `StrategyManager.locate` (`strategies.py:606`) generator | XPath → Text → OCR → Image (sorted by priority in `StrategyFactory`) | strategy yields no result (returns `None` or raises) | raises `OpticsError(Code.E0201, ...)` (`:624`) |
| 3 — driver / source | `InstanceFallback.instances` (`base_factory.py:207`) | Enabled instances in `config.yaml` order | call-site `try/except` per instance | call-site raises (varies) |

These chains are independent. `_try_execute_with_fallback` only sees the keyword raising; whether that raise came from the strategy ladder (`E0201`/`X0201`) or from the driver ladder or somewhere else is what `Code` discriminates.

## Keyword entry points (there are seven)

Adding a method to an API class is **not enough** if the keyword should be reachable from every surface.

1. **CSV/YAML test runner** — `RunnerFactory.create_runner` (`execution.py:211`) builds a `KeywordRegistry` and calls `register(instance)` (`keyword_register.py:22`) on each API class. New public methods on `ActionKeyword` / `AppManagement` / `Verifier` / `FlowControl` are picked up automatically. CSV/YAML caller text is `Title Case Words`; the lookup normalises via `func_name = "_".join(name.split()).lower()` (`test_runnner.py:340`).
2. **Public Python SDK / Robot Framework** — `optics.py:Optics` (`:146`) is a `@library(scope="GLOBAL")` class with an explicit `@keyword("Pretty Name")` method per keyword (e.g. `press_element` at `optics.py:597`, `scroll` at `:789`). Each method `cast`s args and delegates to its `self.action_keyword` / `self.app_management` / etc. instance. Robot Framework picks these up via the `@library` + `@keyword` decorators (no-op fallbacks at `optics.py:50` when `robotframework` isn't installed). **No auto-registration** — new SDK-facing keywords must be added here too. The `@fallback_params` decorator (`optics.py:98`) provides param-level fallback for `fallback_str = Union[str, List[str]]` typed params at the SDK boundary (independent of runner-level fallback).
3. **HTTP server (`optics serve`)** — `helper/serve.py` mounts the FastAPI app from `common/expose_api.py`. `discover_keywords()` (`expose_api.py:366`) reflects over `optics_framework.api.*`; `_extract_keywords_from_class` (`:339`) collects public methods (skipping `_*` and `test*`) and humanises names via `_humanize_keyword` (`:203`). Adding to the API class suffices — but parameter types and docstrings flow into the OpenAPI schema, so keep them accurate. Beyond per-keyword `/v1/sessions/{id}/action`, `POST /v1/dry_run` (`dry_run` endpoint) runs a whole suite in `mode="dry_run"` on an ephemeral session: `_build_dry_run_suite` builds the suite from inline `test_cases`/`modules`/`elements` (`_build_inline_dry_run_suite`) or a server-side `project_path` (`_build_folder_dry_run_suite` → `load_suite_from_folder`), runs it, returns `DryRunResponse`, and terminates the session in `finally`. Any client-supplied `project_path` (here and in `create_session`) is confined by `_safe_project_path` to `OPTICS_PROJECTS_ROOT` (default cwd) to block path traversal.
4. **Code generation (`optics generate`)** — `helper/generate.py` produces pytest or Robot Framework code from CSV/YAML. Three update points:
   - `TestFrameworkGenerator.keyword_registry` dict (`generate.py:199`) — maps `"Press Element" → "press_element"` for both pytest and robot. Without an entry, the generator silently skips the keyword.
   - `PytestGenerator` (`:260`) and `RobotGenerator` (`:380`) — both subclass `TestFrameworkGenerator`; per-framework rendering is in `_generate_module_function` / equivalent.
   - `YAMLDataReader._parse_step` (`:113`) consults a `keyword_registry` set literal inside `read_modules` (`:136`–`:168`) to split YAML step text into `(keyword, params)`. Multi-word names must be added or the longest-match parse will mis-split them.
5. **`optics list` CLI** — `helper/list_keyword.py:7` `list_api_methods` walks `optics_framework.api` via `pkgutil.iter_modules` and `inspect.getmembers`. API class methods show up automatically; `Optics`-facade-only keywords do not.
6. **Robot Framework library import** — when consumers do `Library    optics_framework.optics.Optics`, only methods decorated with `@keyword(...)` on the `Optics` class are exposed. Same source as (2).
7. **Interactive `optics live`** — `LiveController._build_registry` (`helper/live.py:354`) builds its own `KeywordRegistry` via `session.optics.build(ActionKeyword/AppManagement/Verifier)` — the **same auto-registration path as the runner** (entry point 1), so new public API-class methods are reachable in the REPL/TUI automatically and need no live-specific wiring. The natural-language mode reaches the same `keyword_map` through `NaturalLanguageAgent` (see the live-session section below).

### Checklist when adding a keyword to `ActionKeyword`

- Add `def <name>(...)` on `ActionKeyword` (`api/action_keyword.py:146`). Auto-registered for the CSV/YAML runner.
- If it locates an element, decorate with `@with_self_healing` (`action_keyword.py:120`) so it routes through `StrategyManager`, gets the AOI / screenshot-resilience path, and gets the `_try_results_until_success` retry. The wrapped function must accept `located` as a keyword-only param (see `press_element` at `:184`).
- Add a wrapper on `optics.py:Optics` with `@keyword("Pretty Name")` and (if it has element/fallback args) `@fallback_params` — without this it won't be reachable from Robot Framework or the public SDK.
- Add `"Pretty Name": "method_name"` to `TestFrameworkGenerator.keyword_registry` (`generate.py:199`) so `optics generate` emits it.
- Add `"Pretty Name"` to the `keyword_registry` set inside `YAMLDataReader.read_modules` (`generate.py:136`) so YAML step parsing recognises multi-word names.
- Re-run `optics list` to confirm reflection picks it up; `tests/feature/` and `tests/units/` may need fixtures.

## Where else to put things

- **A new driver backend:** `optics_framework/engines/drivers/<name>.py` subclassing `DriverInterface` (`common/driver_interface.py`). `DeviceFactory` (`common/factories.py:10`) discovers it via `GenericFactory.create_instance_dynamic` (`common/base_factory.py:14`). Set `NAME = "<name>"` class attr if any element source needs to match against it via `REQUIRED_DRIVER_TYPE` (matching at `factories.py:60`; example `Appium.NAME = "appium"` at `engines/drivers/appium.py:25`).
- **A new element source:** `optics_framework/engines/elementsources/<name>.py` implementing `ElementSourceInterface`. Set `REQUIRED_DRIVER_TYPE = "appium"` (or similar) so `ElementSourceFactory._find_matching_driver` (`factories.py:60`) injects the matching driver as `driver=` (example: `AppiumFindElement.REQUIRED_DRIVER_TYPE = "appium"` at `engines/elementsources/appium_find_element.py:13`). Implement the methods the strategies you want check via `_is_method_implemented` — `locate` for XPath/Text strategies, `capture` for Text/Image detection and screenshot strategy, `get_page_source` for pagesource.
- **A new OCR / image detector:** drop into `engines/vision_models/ocr_models/` or `…/image_models/` implementing `TextInterface` / `ImageInterface` (`common/text_interface.py`, `common/image_interface.py`). Picked up by `TextFactory` / `ImageFactory` (`factories.py:81`, `:90`).
- **A new LLM backend (for natural-language `optics live`):** `optics_framework/engines/llm_models/<name>.py` subclassing `LLMInterface` (`common/llm_interface.py:7`, abstract `generate` + concrete `generate_json` JSON-coercion helper). Set `NAME = "<name>"` (example: `GeminiLLM.NAME = "gemini"` at `engines/llm_models/gemini.py:34`). Picked up by `LLMFactory` (`factories.py:100`, `DEFAULT_PACKAGE = "optics_framework.engines.llm_models"`). Add a default-disabled entry in `Config.__init__` (`config_handler.py:72`) and an example block in samples (`samples/contact/config.yaml` `llm_models:`).
- **A new CLI subcommand:** subclass `Command` in `helper/cli.py:16`, append to `commands` list at `cli.py:341`. Per-command Pydantic args models live alongside (e.g. `ExecuteArgs` at `cli.py:242`, `LiveArgs` at `cli.py:293`).
- **A new error code:** extend `Code` enum (`common/error.py:34`) and add an `ErrorSpec` entry to `ERROR_REGISTRY` (`error.py:107`). To participate in **fallback level 1**, use the `E02xx` prefix (matched at `test_runnner.py:437`).
- **A new event subscriber (custom reporter):** subclass `EventSubscriber` (`events.py:64`), subscribe via `EventManager.subscribe(subscriber_id, instance)` (`events.py:137`). Implement `close()` if you hold file handles — `EventManager.shutdown` (`:158`) calls it.
- **A new config field:** extend `Config` (`common/config_handler.py:22`); defaults backfilled in `Config.__init__` (`:42`); `deep_merge` (`:82`) handles project-over-global layering in `ConfigHandler.load` (`:142`).
- **A new strategy:** subclass `LocatorStrategy` (`strategies.py:26`); register `(cls, element_type, kwargs, priority)` tuple into `StrategyFactory._registry` (`:475`). Implement `locate`, `assert_elements`, and the static `supports`. A vision-style strategy should also expose `locate_with_aoi` to participate in the AOI path.
- **A new project sample / template:** add a directory under `optics_framework/samples/` (siblings of `contact/`, `youtube/`, `calendar/`, `clock/`, `gmail_web/`, `playwright/`) with `config.yaml`, `test_cases/*.csv`, `modules/*.csv`, `test_data/`. Surfaced via `optics init --template <dirname>`.

## Key data structures

- **Linked-list execution graph** — `TestCaseNode` (`models.py:69`) → `ModuleNode` (`:34`) → `KeywordNode` (`:28`); all carry `state: State` (`:9`) which mirrors `EventStatus` (`events.py:13`). No helper iterators — traverse with `current = head; while current: ...; current = current.next`.
- **`ElementData.elements: Dict[str, List[str]]`** (`models.py:154`) — each element name maps to an ordered fallback list. `get_element(name)` returns the list (used by execute), `get_first(name)` returns first only (used by dry-run / `resolve_param`).
- **`OpticsError` codes** (`error.py:34`; severities `E` / `W` / `X`):
  - `E0201` element not found in one source → triggers fallback **levels 1 and 2**.
  - `X0201` element not found after all fallbacks → also keeps fallback ladder going.
  - `E0303` screenshot empty/black → `ScreenshotStrategy.capture` (`strategies.py:455`).
  - `E0402` keyword name not in `keyword_map`.
  - `E0501` config / required-files missing.
  - `E0701` execution failed (top-level wrap in `ExecutionEngine`).
  - `E0702` test case / session missing.
- **`Event`** (`events.py:33`) carries `entity_type ∈ {"execution","test_case","module","keyword","session"}`, `entity_id`, `parent_id`, `status`, `args`, `start_time`/`end_time`/`elapsed`, `logs` (captured via `LogCaptureBuffer`, `Junit_eventhandler.py:93`).
- **`LocateResult`** (`strategies.py:507`) — `(value, strategy, annotated_frame, is_coordinates)`. `value` is either a `(x, y)` tuple (vision strategies) or a backend element handle (XPath / Text strategies); callers check `is_coordinates`.

## Engine wiring

`Session.__init__` (`session_manager.py:99`) builds `OpticsBuilder` (`optics_builder.py:28`); `add_driver` / `add_element_source` / `add_text_detection` / `add_image_detection` / `add_llm` (`optics_builder.py:108`) stash normalised configs (LLM config comes from the `llm_models` block, gathered via `_get_enabled_config_list(self.config, "llm_models")` at `session_manager.py:125`/`:138`). `OpticsBuilder.get_*` lazy-instantiate by delegating to the matching factory in `common/factories.py`; `get_llm` (`optics_builder.py:187`) → `instantiate_llm` (`:158`) → `LLMFactory.get_driver`. `GenericFactory.create_instance_dynamic` (`base_factory.py:14`) imports `optics_framework.engines.<package>.<name>` and calls `__init__` with the config dict (drivers also get `event_sdk`; element sources get matched `driver=`).

Multiple enabled entries per factory become an `InstanceFallback` (`base_factory.py:207`) — **fallback level 3**. API classes (`ActionKeyword` etc.) receive the builder in `__init__` and call `builder.get_*()` lazily, so engines aren't instantiated until needed.

## Output artefacts

For an `execute` run, `config.execution_output_path` (default `<project>/execution_output/`, ensured in `config_handler.py:120`) receives:
- `junit_output.xml` — written incrementally by `JUnitEventHandler` (`Junit_eventhandler.py:110`), flushed in `flush()` (`:253`), finalised in `close()` (`:268`).
- `logs.json` — when `config.json_log: true`; path set by `_maybe_setup_junit` (`session_manager.py:40`).
- screenshots — saved by `ActionKeyword._save_screenshot_if_available` (`:177`), AOI overlays by `_maybe_save_aoi_screenshot` (`:30`), strategy-annotated frames by `_save_annotated_for_result` (`:52`). Element bboxes come back in the driver's **window coordinate space**, so they are scaled to the screenshot's **pixel space** via `utils.scale_bboxes_for_screenshot` (`common/utils.py`) before drawing (call site in `strategies.py`); skipping this skews annotations on high-DPI / scaled displays.

## Hard rules

1. **Run pre-commit before committing.** `.pre-commit-config.yaml` chains ruff (`--fix`), bandit (excluding `tests/`), trailing-whitespace, end-of-file-fixer, check-yaml, check-json, commitizen (commit-msg), gitleaks. Run `poetry run pre-commit run --files <changed>` or let the git hook fire — never `--no-verify`.
2. **Trace the source and blast radius before changing a function.** `grep -rn "<name>" optics_framework/ tests/` for every symbol you touch. Specifically:
   - Renaming a public method on `ActionKeyword` / `AppManagement` / `Verifier` / `FlowControl` silently breaks every CSV/YAML that names it **and** every wrapper in `optics.py:Optics` **and** the `keyword_registry` dicts in `helper/generate.py` (line `:199` and `:136`).
   - `Code.<E…>` consumers: the `E02*` / `X0201` prefix in `test_runnner.py:437` is load-bearing for fallback semantics — don't reuse the prefix for non-element errors.
   - `Event.entity_type` values: `JUnitEventHandler._handle_test_case_event` / `_handle_module_event` / `_handle_keyword_event` (`Junit_eventhandler.py:153`/`:187`/`:202`) dispatch on them.
   - `LocatorStrategy.supports` predicates: changing how `determine_element_type` (`utils.py:135`) classifies a string changes which strategy fires.
3. **Conventional Commits.** `.cz.toml` configures commitizen; the commit-msg hook rejects anything that isn't `feat:` / `fix:` / `refactor:` / `docs:` / `chore:` / `test:` / `perf:` / `style:` / `build:` / `ci:`.
4. **Python 3.12+ only.** `pyproject.toml:25` pins `python = ">=3.12,<4.0"`. Use modern typing (`list[str]`, `X | None`, `ParamSpec`, `Self`); `typing_extensions` is available for newer features.
5. **Do not edit generated trees.** `__pycache__/`, `dist/`, `docs/build/`, `.tox/`, `htmlcov/`, `execution_output/` are runtime artefacts.

## Pitfalls

- **Two `${name}` resolution functions.** `TestRunner.resolve_param` (`test_runnner.py:187`) returns the *first* value; `_build_param_candidates` (`:378`) returns the *list*. Dry-run uses first form, execute uses list form — divergent dry-run-vs-execute behaviour usually traces here.
- **Three fallback ladders, three places they trigger.** See the table above. Symptoms: "element not found after retries" almost always means level 2 (strategy) is exhausting before level 1 (param) gets a chance to retry — verify by checking `execution_tracer` logs to see which strategies fired.
- **`KeywordRegistry.register` is greedy.** It registers every public callable on the instance — adding a public helper to `ActionKeyword` instantly exposes it as a keyword and may collide silently across API classes (only a warning at `keyword_register.py:36`). Prefix helpers with `_`.
- **`@with_self_healing` requires `located` kwarg.** The decorator at `action_keyword.py:120` passes `located=result.value`; if your wrapped function doesn't accept it as keyword-only, you'll get a `TypeError` that masquerades as a strategy failure.
- **`optics.py:Optics` ≠ runner's keyword set.** The runner builds its own `KeywordRegistry` from API classes (auto-discovered); the SDK builds its own from `Optics`-class methods (explicit). They can drift. The HTTP API (`expose_api.discover_keywords`) and `optics list` reflect the API classes directly, so they match the runner — not the SDK.
- **Async re-entrancy.** `ExecutionEngine.execute` and below must run inside `asyncio.run(...)` — `BaseRunner.run` is the only sanctioned entry. `PytestRunner` (`test_runnner.py:758`) uses `queue_event_sync` (`:79`) which spawns `asyncio.run` per event; do not mix the two paths in one flow.
- **`Session` mutates the passed-in `Config`.** `_maybe_setup_junit` (`session_manager.py:40`) sets `config.json_path`; `ConfigHandler.__init__` (`config_handler.py:118`) sets `execution_output_path` and creates the directory. Don't share a `Config` instance across sessions.
- **`OPTICS_EVENT_DRAIN_TIMEOUT_S`** (`execution.py:266`, default `2.0`) caps shutdown-side event flushing. Truncated JUnit XML on long runs → raise this env before debugging logic.
- **Robot Framework is optional.** `optics.py:50` provides no-op `keyword` / `library` decorators when `robotframework` isn't installed. Don't hard-import `robot.api`; mirror the try/except.
- **`element_source.locate` stubs hide strategies.** `LocatorStrategy._is_method_implemented` (`strategies.py:65`) reads source to detect bodies that just `raise NotImplementedError` and excludes that source from the strategy. A poorly-stubbed new element source can silently disappear from the strategy list.
- **Two test trees.** `tests/units/` and `tests/feature/` both run under one `pytest` invocation (`pyproject.toml:73`, `testpaths = ["tests"]`). Markers (`white_box`, `black_box`, `hybrid`, `generate`) scope: `pytest -m white_box`. `tests/conftest.py` injects shared fixtures.

## Commands

```bash
poetry install --with dev,test,docs       # full setup
poetry run pytest                          # tests + coverage (configured in pyproject.toml)
poetry run pytest -m white_box             # unit subset
poetry run ruff check --fix .              # lint + autofix
poetry run pre-commit run --files <paths>  # hook chain on touched files
poetry run mkdocs serve                    # docs preview
poetry build                               # wheel + sdist
optics execute <folder>                    # smoke a project against the installed CLI
optics list                                # print discoverable API keywords (reflection)
optics generate <folder>                   # emit pytest/robot code from CSV/YAML
optics serve                               # FastAPI server exposing keyword endpoints
optics live [folder]                       # interactive REPL/TUI: run keywords (or NL) against a live target
```

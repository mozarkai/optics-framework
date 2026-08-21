# Phase 0 — Session Leaks & Races Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the nine defects that make two concurrent `optics serve` sessions corrupt each other or leak resources, with no new dependencies and no API-shape change.

**Architecture:** Pure local-correctness work on the existing single-process server. Request-scoped state moves off the `Session` object onto a `ContextVar`; teardown moves into `finally` blocks; the per-session `keyword_lock` is extended to cover the paths that currently bypass it; the per-session `EventManager` lifecycle moves from per-request to per-session. Nothing here anticipates Redis — Phase 4 builds on it, but every task below stands alone.

**Tech Stack:** Python 3.12+, FastAPI/starlette, asyncio, pytest, poetry.

**Spec:** [`docs/superpowers/specs/2026-08-18-parallel-sessions-stateless-serve-design.md`](../specs/2026-08-18-parallel-sessions-stateless-serve-design.md) §11 (Lifecycle), §5 (SessionRecord — the `request_template_overrides` removal), §4.2 (SessionLease — the lock this task extends). Findings referenced by id come from [`docs/superpowers/specs/2026-08-18-parallel-sessions-audit.md`](../specs/2026-08-18-parallel-sessions-audit.md).

## Global Constraints

- Python `>=3.12,<4.0` (`pyproject.toml:25`). Use modern typing (`list[str]`, `X | None`, `Self`).
- Conventional Commits enforced by the commitizen commit-msg hook: `feat:` / `fix:` / `refactor:` / `docs:` / `chore:` / `test:` / `perf:` / `style:` / `build:` / `ci:`.
- **Never** add `Co-Authored-By: Claude` or any AI-attribution trailer to a commit message or PR body.
- Run pre-commit before each commit. Never `--no-verify`. **Two environment quirks, both verified:** (a) `pre-commit run` accepts **at most one hook id per invocation** — passing several fails with `unrecognized arguments`, and the hooks then do not run at all; loop instead. (b) the `gitleaks` and `commitizen` hook environments fail to install here with `SSL: CERTIFICATE_VERIFY_FAILED` (pre-existing local condition, reproduced in and out of the sandbox — do not debug it). So use:
```bash
for h in ruff bandit trailing-whitespace end-of-file-fixer; do \
  poetry run pre-commit run "$h" --files <changed files>; done
```
and state in your report which hooks ran and which were unavailable. Do not silently skip verification.
- Tests live in two trees under one pytest invocation (`pytest.toml`, `testpaths = ["tests"]`). Server tests belong in `tests/units/common/test_expose_api.py` unless a new file is specified.
- Server tests must stay **hermetic and device-less** — no driver or engine is ever instantiated. Follow the existing conventions in `tests/units/common/test_expose_api.py`: `fastapi.testclient.TestClient` with `session_manager` / `execute_keyword` / `ExecutionEngine` / `KeywordRegistry` mocked, and the local `_run(coro)` helper for driving coroutines directly.
- Do not touch `__pycache__/`, `dist/`, `docs/build/`, `.tox/`, `htmlcov/`, `execution_output/`.
- Branch is already created: `feat/parallel-sessions-stateless-serve`. Commit onto it; do not create sub-branches.

---

### Task 1: `delete_session` must always evict the session (finding `G1`)

Today `delete_session` runs `close_and_terminate_app` first and only reaches `session_manager.terminate_session` if that succeeds. When a device reboots or the Appium server restarts, `driver.quit()` raises, the endpoint returns 500, and the session is **never** removed from `session_manager.sessions`, `workspace_hashes`, or the event-manager registry — and its temp dir is never removed. Retrying produces the identical 500. There is no other eviction path.

`session_manager.terminate_session` (`optics_framework/common/session_manager.py:171-185`) already calls `session.driver.terminate()` itself, so the `close_and_terminate_app` pre-call is both redundant and the sole reason cleanup gets skipped.

**Files:**
- Modify: `optics_framework/common/expose_api.py:1323-1350`
- Test: `tests/units/common/test_expose_api.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: no new symbols. `delete_session(session_id: str) -> TerminationResponse` keeps its signature and its `TerminationResponse` return.

- [ ] **Step 1: Write the failing test**

```python
def test_delete_session_evicts_even_when_driver_teardown_fails():
    """G1: a driver that refuses to quit must not make a session un-evictable."""
    mgr = MagicMock()
    mgr.get_session.return_value = MagicMock()
    mgr.terminate_session = MagicMock()

    # Inject the failure into terminate_session, NOT execute_keyword: this fix
    # deliberately removes the close_and_terminate_app pre-call, so execute_keyword
    # is never invoked on this path and patching it would test nothing.
    mgr.terminate_session = MagicMock(side_effect=RuntimeError("device rebooted"))

    with patch.object(expose_api, "session_manager", mgr):
        expose_api.workspace_hashes["sess-broken"] = "deadbeef"
        with pytest.raises(HTTPException) as exc:
            _run(expose_api.delete_session("sess-broken"))

    # The caller still learns it failed...
    assert exc.value.status_code == 500
    # ...but the session is gone regardless.
    mgr.terminate_session.assert_called_once_with("sess-broken")
    assert "sess-broken" not in expose_api.workspace_hashes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/units/common/test_expose_api.py::test_delete_session_evicts_even_when_driver_teardown_fails -v`
Expected: FAIL — `workspace_hashes` still contains `sess-broken`, because the pre-fix code raises `HTTPException` from the `close_and_terminate_app` path and never reaches the cleanup lines.

- [ ] **Step 3: Write minimal implementation**

Replace the body of `delete_session` (`optics_framework/common/expose_api.py:1323` onward). The `close_and_terminate_app` pre-call is dropped entirely — `terminate_session` performs the driver teardown.

```python
async def delete_session(session_id: str):
    """
    Terminate the specified session and clean up resources.

    Cleanup runs unconditionally: a driver that fails to quit (rebooted device,
    restarted Appium server) must never leave the session un-evictable, since
    this endpoint is the only eviction path. The caller still receives the
    failure, but the server state is always consistent afterwards.
    """
    teardown_error: Exception | None = None
    try:
        await asyncio.to_thread(session_manager.terminate_session, session_id)
    except Exception as e:  # noqa: BLE001 - reported below, never suppressed
        teardown_error = e
        internal_logger.warning(
            "Driver teardown failed for session %s; session evicted anyway: %s",
            session_id, e,
        )
    finally:
        workspace_hashes.pop(session_id, None)

    if teardown_error is not None:
        if isinstance(teardown_error, OpticsError):
            raise HTTPException(
                status_code=teardown_error.status_code,
                detail=teardown_error.to_payload(include_status=True),
            ) from teardown_error
        raise HTTPException(
            status_code=500,
            detail=f"{MSG_SESSION_TERMINATION_FAILED} {teardown_error}",
        ) from teardown_error

    internal_logger.info("Terminated session: %s", session_id)
    return TerminationResponse()
```

Note the `asyncio.to_thread` wrapper: `terminate_session` performs `driver.quit()`, an `EventSDK` retry loop containing `time.sleep`, and a `shutil.rmtree` — up to ~36s of blocking work that currently runs on the event loop (finding `A2`). Wrapping it here closes `A2` as a side effect of the `G1` fix, because the two touch the same three lines.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/units/common/test_expose_api.py -v -k delete_session`
Expected: PASS, including any pre-existing `delete_session` tests. If a pre-existing test asserted that `execute_keyword` is called with `close_and_terminate_app`, update it — that call is deliberately removed, and say so in the commit body.

- [ ] **Step 5: Commit**

```bash
for h in ruff bandit trailing-whitespace end-of-file-fixer; do \
  poetry run pre-commit run "$h" --files optics_framework/common/expose_api.py tests/units/common/test_expose_api.py; done
git add optics_framework/common/expose_api.py tests/units/common/test_expose_api.py
git commit -m "fix(serve): always evict a session on delete, even if teardown fails"
```

---

### Task 2: `create_session` must not leak a half-built session (finding `G2`)

`create_session` registers the session, then auto-launches the app (`optics_framework/common/expose_api.py:529-534`). If `launch_app` raises, the `except Exception` handler at `:542` returns a 500 **without terminating the session**, which may hold a half-open Appium session — and the device stays occupied with no eviction path (Task 1 fixes the path, but the session id is never returned to the caller, so nobody can call it).

Second defect in the same handler: `execute_keyword` raises `HTTPException`, and `except Exception` catches it, flattening a precise 4xx into a generic `500 "Session creation failed: ..."`.

**Files:**
- Modify: `optics_framework/common/expose_api.py:513-544`
- Test: `tests/units/common/test_expose_api.py`

**Interfaces:**
- Consumes: `delete_session`'s guarantee from Task 1 is *not* used here — this task calls `session_manager.terminate_session` directly, because the session id was never handed to the client.
- Produces: no new symbols. `create_session(config: SessionConfig) -> SessionResponse` unchanged.

- [ ] **Step 1: Write the failing tests**

Two behaviours, two tests.

```python
def _minimal_session_config():
    """Smallest SessionConfig that normalizes to one enabled driver source."""
    return expose_api.SessionConfig(
        driver_sources=[{"appium": {"enabled": True, "url": "http://127.0.0.1:4723"}}],
        elements_sources=[],
        text_detection=[],
        image_detection=[],
    )


def test_create_session_terminates_when_app_launch_fails():
    """G2: a failed auto-launch must not leave a registered session behind."""
    mgr = MagicMock()
    mgr.create_session = MagicMock(return_value="sess-half-built")
    mgr.terminate_session = MagicMock()

    with patch.object(expose_api, "session_manager", mgr), \
         patch.object(
             expose_api, "execute_keyword",
             AsyncMock(side_effect=RuntimeError("app not installed")),
         ), \
         patch.object(expose_api, "reconfigure_logging", MagicMock()):
        with pytest.raises(HTTPException):
            _run(expose_api.create_session(_minimal_session_config()))

    mgr.terminate_session.assert_called_once_with("sess-half-built")


def test_create_session_preserves_http_status_from_launch():
    """G2: an HTTPException from launch must not be flattened into a 500."""
    mgr = MagicMock()
    mgr.create_session = MagicMock(return_value="sess-1")
    mgr.terminate_session = MagicMock()

    with patch.object(expose_api, "session_manager", mgr), \
         patch.object(
             expose_api, "execute_keyword",
             AsyncMock(side_effect=HTTPException(status_code=404, detail="no such keyword")),
         ), \
         patch.object(expose_api, "reconfigure_logging", MagicMock()):
        with pytest.raises(HTTPException) as exc:
            _run(expose_api.create_session(_minimal_session_config()))

    assert exc.value.status_code == 404
    assert exc.value.detail == "no such keyword"
    mgr.terminate_session.assert_called_once_with("sess-1")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/units/common/test_expose_api.py -v -k "create_session_terminates or create_session_preserves"`
Expected: FAIL. First test: `terminate_session` called 0 times. Second test: `assert exc.value.status_code == 404` fails with `500`, because `except Exception` at `:542` swallows the `HTTPException`.

- [ ] **Step 3: Write minimal implementation**

Two edits inside `create_session`.

First, wrap the launch so a failure cleans up. Replace lines `529-538` (the `launch_request` block through the `return SessionResponse(...)`):

```python
        launch_request = ExecuteRequest(
            mode=MODE_KEYWORD,
            keyword=KEYWORD_LAUNCH_APP,
            params=[]
        )
        try:
            driver_session = await execute_keyword(session_id, launch_request)
        except BaseException:
            # The client never received this session id, so this is the only
            # chance to reclaim the device. Re-raise unchanged so the handlers
            # below classify it, rather than reporting a generic 500.
            await asyncio.to_thread(session_manager.terminate_session, session_id)
            raise
        return SessionResponse(
            session_id=session_id,
            driver_id=(driver_session.data or {}).get(KEY_RESULT)
        )
```

Second, stop the generic handler from swallowing `HTTPException`. Insert a re-raise clause **before** the existing `except OpticsError` clause at `:539`:

```python
    except HTTPException:
        raise
    except OpticsError as e:
        ...  # unchanged
```

`HTTPException` is a subclass of `Exception`, not of `OpticsError`, so clause order is what fixes this. Placing it first is required.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/units/common/test_expose_api.py -v -k create_session`
Expected: PASS, including pre-existing `create_session` tests.

- [ ] **Step 5: Commit**

```bash
for h in ruff bandit trailing-whitespace end-of-file-fixer; do \
  poetry run pre-commit run "$h" --files optics_framework/common/expose_api.py tests/units/common/test_expose_api.py; done
git add optics_framework/common/expose_api.py tests/units/common/test_expose_api.py
git commit -m "fix(serve): reclaim the device when session auto-launch fails"
```

---

### Task 3: Make request template overrides request-scoped (finding `H2`)

`_setup_request_template_overrides` writes into `session.request_template_overrides` — a dict on the **shared** `Session` object (`optics_framework/common/session_manager.py:119`) — *before* the keyword lock is taken, and `execute_keyword`'s `finally` calls `.clear()` on it *outside* the lock. So with two concurrent requests on one session: B writes its override, A completes and clears **everything including B's**, B then resolves nothing, falls through to project templates, and fails with a bogus element-not-found. Or A's `rmtree` removes a directory B is about to read.

The fix is a `ContextVar`. `contextvars` are per-task, so each request gets its own view automatically, and `asyncio.to_thread` propagates the context into the worker thread — which is where the keyword actually reads the resolver.

> **Note for Phase 1:** `loop.run_in_executor` does **not** propagate context the way `asyncio.to_thread` does. When Task 1 of the Phase 1 plan swaps in a per-session executor, it must dispatch via `contextvars.copy_context().run(...)` or this fix silently regresses. That requirement is recorded in the Phase 1 plan; do not change the dispatch mechanism here.

**Files:**
- Modify: `optics_framework/common/session_manager.py:75-97` (`SessionTemplateResolver.get_template_path`), `:119` (remove the field)
- Modify: `optics_framework/common/expose_api.py:834-855` (`_setup_request_template_overrides`), `:938-946` (the `finally`)
- Test: `tests/units/common/test_expose_api_vision.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `optics_framework.common.session_manager.request_template_overrides: ContextVar[dict[str, str]]` — module-level, default `{}`. Read by `SessionTemplateResolver.get_template_path`; set by `expose_api._setup_request_template_overrides`.
  - `_setup_request_template_overrides(template_images: dict[str, str] | None) -> tuple[list[str], contextvars.Token | None]` — **signature changes**: the `session` parameter is removed (it no longer writes to the session) and it now returns the reset token alongside the temp dirs. `Session.request_template_overrides` is **deleted**; anything reading it must be updated.

- [ ] **Step 1: Write the failing test**

```python
def test_request_template_overrides_are_isolated_per_task():
    """H2: one request's overrides must be invisible to a concurrent request."""
    from optics_framework.common import session_manager as sm

    session = MagicMock()
    session.inline_templates = {}
    session.templates = None
    resolver = sm.SessionTemplateResolver(session)

    seen: dict[str, str | None] = {}

    async def one_request(name: str, path: str, settle: asyncio.Event, go: asyncio.Event):
        token = sm.request_template_overrides.set({name: path})
        try:
            settle.set()
            await go.wait()
            # After the sibling request has set *its* overrides, ours must survive.
            seen[name] = resolver.get_template_path(name)
        finally:
            sm.request_template_overrides.reset(token)

    async def scenario():
        a_ready, b_ready, go = asyncio.Event(), asyncio.Event(), asyncio.Event()
        task_a = asyncio.create_task(one_request("login_btn", "/tmp/a.png", a_ready, go))
        task_b = asyncio.create_task(one_request("cancel_btn", "/tmp/b.png", b_ready, go))
        await a_ready.wait()
        await b_ready.wait()
        go.set()
        await asyncio.gather(task_a, task_b)

    asyncio.run(scenario())

    assert seen == {"login_btn": "/tmp/a.png", "cancel_btn": "/tmp/b.png"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/units/common/test_expose_api_vision.py::test_request_template_overrides_are_isolated_per_task -v`
Expected: FAIL with `AttributeError: module 'optics_framework.common.session_manager' has no attribute 'request_template_overrides'`.

- [ ] **Step 3: Write minimal implementation**

In `optics_framework/common/session_manager.py`, add the import and the module-level `ContextVar` above the `SessionTemplateResolver` class:

```python
import contextvars

# Template overrides supplied by a single in-flight request. A ContextVar rather
# than a Session field because two concurrent requests share one Session, and a
# session-level dict lets one request clear or overwrite another's entries.
request_template_overrides: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "request_template_overrides", default={}
)
```

Change `SessionTemplateResolver.get_template_path` to read it:

```python
    def get_template_path(self, name: str) -> Optional[str]:
        """Return path for a template name; checks request overrides, then inline, then project."""
        path = request_template_overrides.get().get(name)
        if path is not None:
            return path
        inline = getattr(self._session, "inline_templates", None) or {}
        path = inline.get(name)
        if path is not None:
            return path
        if self._session.templates is not None:
            return self._session.templates.get_template_path(name)
        return None
```

Delete line `:119` (`self.request_template_overrides: Dict[str, str] = {}`) from `Session.__init__`.

In `optics_framework/common/expose_api.py`, add `import contextvars` and change the helper:

```python
async def _setup_request_template_overrides(
    template_images: Optional[Dict[str, str]]
) -> tuple[List[str], Optional[contextvars.Token]]:
    """Write template images to a temp dir and bind them to this request's context.

    Returns the temp dirs to clean up and the ContextVar token to reset, so the
    caller's ``finally`` can undo exactly what this request set and nothing else.
    """
    if not template_images:
        return [], None
    temp_dir = tempfile.mkdtemp(prefix=TEMP_DIR_PREFIX)
    overrides: Dict[str, str] = {}
    for name, b64_value in template_images.items():
        try:
            safe_stem = _safe_template_filename(name)
        except ValueError as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(e)) from e
        try:
            raw = _decode_template_base64(b64_value)
        except (binascii.Error, ValueError) as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=f"{MSG_INVALID_BASE64_IMAGE} {e}") from e
        path = os.path.join(temp_dir, f"{safe_stem}{TEMPLATE_EXT_PNG}")
        await asyncio.to_thread(_write_bytes_to_path, path, raw)
        overrides[name] = path
    token = request_template_overrides.set(overrides)
    return [temp_dir], token
```

Add the import of the `ContextVar` to `expose_api.py`'s existing `session_manager` import group:

```python
from optics_framework.common.session_manager import request_template_overrides
```

Update the call site in `execute_keyword` (`:896`):

```python
    request_temp_dirs, overrides_token = await _setup_request_template_overrides(
        request.template_images
    )
```

And the `finally` (`:938-939`) — replace the unconditional `.clear()`:

```python
    finally:
        if overrides_token is not None:
            request_template_overrides.reset(overrides_token)
        for dir_path in request_temp_dirs:
            ...  # unchanged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/units/common/test_expose_api_vision.py tests/units/common/test_expose_api.py -v`
Expected: PASS. Then confirm nothing else reads the removed field:

Run: `grep -rn "request_template_overrides" optics_framework/ tests/`
Expected: hits only in `session_manager.py` (the `ContextVar` and the resolver) and `expose_api.py` (the helper, the call site, the `finally`). Any hit on `session.request_template_overrides` or `Session.request_template_overrides` is a miss — fix it.

- [ ] **Step 5: Commit**

```bash
for h in ruff bandit trailing-whitespace end-of-file-fixer; do \
  poetry run pre-commit run "$h" --files optics_framework/common/session_manager.py optics_framework/common/expose_api.py \
          tests/units/common/test_expose_api_vision.py; done
git add optics_framework/common/session_manager.py optics_framework/common/expose_api.py \
        tests/units/common/test_expose_api_vision.py
git commit -m "fix(serve): scope request template overrides to the request, not the session"
```

---

### Task 4: Take `keyword_lock` around the workspace stream (finding `H1`)

`_gather_workspace_data` (`optics_framework/common/expose_api.py:1150-1186`) builds its own `Verifier` and calls screenshot capture, element collection, and page-source capture directly via `asyncio.to_thread`, **never taking `session.keyword_lock`**. A UI streaming a session while anything posts a keyword interleaves WebDriver commands on the same remote session — Appium serializes server-side, but the client sees command reordering and spurious `NoSuchElement` / stale-element failures. There is also a data race on `InstanceFallback.current_instance` (`optics_framework/common/base_factory.py:239`).

**Files:**
- Modify: `optics_framework/common/expose_api.py:1150-1186` (`_gather_workspace_data`)
- Test: `tests/units/common/test_expose_api.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: no signature change. `_gather_workspace_data(session, include_source: bool, filter_config: list[str] | None) -> dict` keeps its shape; it now acquires `session.keyword_lock` internally, so callers need no change.

- [ ] **Step 1: Write the failing test**

```python
def test_gather_workspace_data_holds_the_keyword_lock():
    """H1: the workspace poller must not issue driver commands concurrently with a keyword."""
    session = MagicMock()
    session.session_id = "sess-1"
    session.keyword_lock = asyncio.Lock()

    observed: list[bool] = []

    def _capture_screenshot_np(*_a, **_kw):
        observed.append(session.keyword_lock.locked())
        return None

    verifier = MagicMock()
    verifier._safe_capture_screenshot_np = _capture_screenshot_np
    verifier._collect_interactive_elements = MagicMock(return_value=[])
    session.optics.build.return_value = verifier

    with patch.object(expose_api, "Verifier", MagicMock()):
        _run(expose_api._gather_workspace_data(session, False, None))

    assert observed == [True], "driver work ran without holding session.keyword_lock"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/units/common/test_expose_api.py::test_gather_workspace_data_holds_the_keyword_lock -v`
Expected: FAIL with `AssertionError: driver work ran without holding session.keyword_lock` — `observed == [False]`.

- [ ] **Step 3: Write minimal implementation**

Wrap the body of `_gather_workspace_data` in the lock. The exact shape depends on the current body — read `optics_framework/common/expose_api.py:1150-1186` and hoist everything that touches the driver inside:

```python
async def _gather_workspace_data(
    session: Session,
    include_source: bool = False,
    filter_config: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Capture screenshot, elements, and optionally page source for one stream tick.

    Holds session.keyword_lock: a WebDriver session is not concurrency-safe, and
    without this the poller's commands interleave with an in-flight keyword's,
    producing spurious stale-element failures.
    """
    async with session.keyword_lock:
        ...  # existing body, unchanged
```

Do **not** move `_compute_workspace_hash` inside the lock — it is pure computation on already-captured data and holding the lock across it needlessly delays keywords.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/units/common/test_expose_api.py -v -k "workspace"`
Expected: PASS.

Then verify the stream still terminates on session death, because the lock introduces a new await point where cancellation can land:

Run: `poetry run pytest tests/units/common/test_expose_api.py -v -k "stream or generator"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
for h in ruff bandit trailing-whitespace end-of-file-fixer; do \
  poetry run pre-commit run "$h" --files optics_framework/common/expose_api.py tests/units/common/test_expose_api.py; done
git add optics_framework/common/expose_api.py tests/units/common/test_expose_api.py
git commit -m "fix(serve): serialize workspace stream capture against keyword execution"
```

---

### Task 5: Guard the `session.apis` swap and check stream liveness (findings `H3`, `G7`)

Two small, independent fixes folded into one task because they touch adjacent code and share a test file.

`add_session_api` does `session.apis = api_data` (`optics_framework/common/expose_api.py:1004`) with no synchronization, so a concurrent in-flight API keyword can read a swapped-out collection mid-execution.

`event_generator` (`:1263-1314`) loops forever on `session.event_queue` and never checks whether the session still exists, so after termination it keeps heartbeating against a dead `Session` and holds it alive through the closure. `workspace_generator` already does this check at `:1220`; the two are simply inconsistent.

**Files:**
- Modify: `optics_framework/common/expose_api.py:988-1010` (`add_session_api`), `:1263-1314` (`event_generator`)
- Test: `tests/units/common/test_expose_api.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: no signature changes.

- [ ] **Step 1: Write the failing tests**

```python
def test_add_session_api_holds_the_keyword_lock():
    """H3: swapping session.apis must not race an in-flight API keyword."""
    observed: list[bool] = []

    class _Session:
        """Real object, not a MagicMock: the apis setter must actually fire."""
        def __init__(self):
            self.session_id = "sess-1"
            self.keyword_lock = asyncio.Lock()
            self._apis = None

        @property
        def apis(self):
            return self._apis

        @apis.setter
        def apis(self, value):
            observed.append(self.keyword_lock.locked())
            self._apis = value

    session = _Session()
    mgr = MagicMock()
    mgr.get_session.return_value = session

    with patch.object(expose_api, "session_manager", mgr), \
         patch.object(
             expose_api, "_parse_api_data_to_model",
             MagicMock(return_value="parsed-api-data"),
         ):
        _run(expose_api.add_session_api("sess-1", expose_api.ApiRequest(api_data={})))

    assert session.apis == "parsed-api-data", "the swap did not happen at all"
    assert observed == [True], "session.apis was swapped without holding keyword_lock"


def test_event_generator_stops_when_session_is_gone():
    """G7: the event stream must not outlive its session."""
    session = MagicMock()
    session.session_id = "sess-1"
    session.event_queue = asyncio.Queue()

    mgr = MagicMock()
    mgr.get_session.return_value = None  # already terminated

    async def drain():
        return [chunk async for chunk in expose_api.event_generator(session)]

    with patch.object(expose_api, "session_manager", mgr):
        chunks = _run(asyncio.wait_for(drain(), timeout=2.0))

    assert chunks == [], "generator yielded after the session was terminated"
```

The `_Recorder` descriptor in the first test needs `session` to be a real object rather than a `MagicMock` for `__set__` to fire. Use a small stand-in instead:

```python
    class _Session:
        def __init__(self):
            self.keyword_lock = asyncio.Lock()
            self._apis = None
        @property
        def apis(self):
            return self._apis
        @apis.setter
        def apis(self, value):
            observed.append(self.keyword_lock.locked())
            self._apis = value
```

Use `_Session()` as the session in that test.

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/units/common/test_expose_api.py -v -k "add_session_api_holds or event_generator_stops"`
Expected: FAIL. First: `observed == [False]`. Second: `asyncio.TimeoutError` after 2s, because the generator loops forever.

- [ ] **Step 3: Write minimal implementation**

In `add_session_api`, wrap the assignment:

```python
    async with session.keyword_lock:
        session.apis = api_data
```

In `event_generator`, add the liveness check at the top of the loop body, mirroring `workspace_generator:1220`:

```python
    while True:
        if not session_manager.get_session(session.session_id):
            internal_logger.warning(
                "Session %s no longer exists, ending event stream", session.session_id
            )
            break
        ...  # existing body
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/units/common/test_expose_api.py -v`
Expected: PASS (whole file, to catch any stream test that relied on the infinite loop).

- [ ] **Step 5: Commit**

```bash
for h in ruff bandit trailing-whitespace end-of-file-fixer; do \
  poetry run pre-commit run "$h" --files optics_framework/common/expose_api.py tests/units/common/test_expose_api.py; done
git add optics_framework/common/expose_api.py tests/units/common/test_expose_api.py
git commit -m "fix(serve): guard api-data swap and end event stream with its session"
```

---

### Task 6: Move the `EventManager` lifecycle off the per-request path (finding `B3`)

`ExecutionEngine.execute` calls `event_manager.start()` (`optics_framework/common/execution.py:396`) and, in its `finally`, `_drain_events_and_shutdown` (`:430`) which ends in `event_manager.shutdown()` → `stop()` → cancels `_process_task` (`optics_framework/common/events.py:92-97`). The manager is **session-scoped** and persists in the registry, but its dispatch task is torn down per keyword request.

`session.keyword_lock` covers only `execution.py:189-190`, not the surrounding engine body. So with two concurrent requests on one session: B enters and calls `start()` (a no-op, already running); A finishes and its `finally` cancels the shared `_process_task`; B then publishes into a queue with `_running == False` and no consumer → **the event is never dispatched**, and B's drain loop spins the full `OPTICS_EVENT_DRAIN_TIMEOUT_S` (2s) waiting for a queue nobody reads. Every request also pays `asyncio.sleep(0.1)` drain granularity in the happy path.

The fix: the engine drains but never shuts down. Shutdown belongs to session teardown, where `EventManagerRegistry.remove_session` already runs — it currently calls `manager.stop()`, which skips subscriber `close()`.

**Ordering hazard.** `SessionManager.terminate_session` (`optics_framework/common/session_manager.py:184-185`) calls `cleanup_junit(session_id)` and *then* `remove_session`. If `remove_session` starts calling `shutdown()` (which closes subscribers, including the JUnit handler), the JUnit handler gets closed twice. Verify `JUnitEventHandler.close` is idempotent (`optics_framework/common/Junit_eventhandler.py:287`); if it is not, make it so, and cover it with a test. Do not reorder the two calls without checking what `cleanup_junit` does that `shutdown` does not.

**Files:**
- Modify: `optics_framework/common/execution.py:371-387` (`_drain_events_and_shutdown`), `:430`
- Modify: `optics_framework/common/events.py:195-199` (`EventManagerRegistry.remove_session`)
- Modify (only if not already idempotent): `optics_framework/common/Junit_eventhandler.py:287`
- Test: `tests/units/common/test_execution_threading.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `ExecutionEngine._drain_events(event_manager: EventManager) -> None` — **renamed** from `_drain_events_and_shutdown`; drains only, never shuts down. Any test referencing the old name must be updated.
  - `EventManagerRegistry.remove_session(session_id: str) -> None` — unchanged signature; now calls `manager.shutdown()` instead of `manager.stop()`.

- [ ] **Step 1: Write the failing test**

```python
def test_concurrent_keywords_on_one_session_keep_event_dispatch_alive():
    """B3: one request completing must not cancel the shared dispatch task."""
    from optics_framework.common.events import EventManager

    async def scenario():
        mgr = EventManager()
        mgr.start()
        delivered: list[str] = []

        class _Sub:
            async def on_event(self, event):
                delivered.append(event)

        mgr.subscribe("probe", _Sub())

        engine = ExecutionEngine(MagicMock())
        # Request A finishes and drains.
        await engine._drain_events(mgr)
        # Request B, still in flight, publishes afterwards.
        await mgr.event_queue.put("event-from-B")
        await asyncio.sleep(0.2)
        running = mgr._running
        mgr.shutdown()
        return delivered, running

    delivered, running = asyncio.run(scenario())
    assert running is True, "dispatch loop was stopped by a sibling request"
    assert delivered == ["event-from-B"], "event published after a sibling drained was lost"
```

Import `ExecutionEngine` from `optics_framework.common.execution` at the top of the test file if it is not already imported.

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/units/common/test_execution_threading.py::test_concurrent_keywords_on_one_session_keep_event_dispatch_alive -v`
Expected: FAIL with `AttributeError: 'ExecutionEngine' object has no attribute '_drain_events'`.

- [ ] **Step 3: Write minimal implementation**

In `optics_framework/common/execution.py`, rename the method and drop the final `shutdown()` call:

```python
    async def _drain_events(self, event_manager: EventManager) -> None:
        """Wait for the event queue to drain, with a timeout.

        Deliberately does NOT shut the manager down: it is session-scoped and
        shared by every concurrent request on that session, so tearing it down
        here cancels a sibling request's dispatch task and silently drops its
        events. Shutdown belongs to session teardown, in
        EventManagerRegistry.remove_session.
        """
        internal_logger.debug(
            "Event queue size before drain: %d", event_manager.event_queue.qsize()
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._event_drain_timeout_s
        while event_manager.event_queue.qsize() > 0:
            if loop.time() >= deadline:
                internal_logger.warning(
                    "Event drain timed out after %.2fs; proceeding. Remaining events: %d",
                    self._event_drain_timeout_s,
                    event_manager.event_queue.qsize(),
                )
                break
            await asyncio.sleep(0.1)
```

Note `get_event_loop()` → `get_running_loop()` — this method is only ever called from a coroutine, and the deprecated form is finding `A15`.

Update the call site at `:430`:

```python
                await self._drain_events(event_manager)
```

In `optics_framework/common/events.py`, `EventManagerRegistry.remove_session`, change `manager.stop()` to `manager.shutdown()`:

```python
                manager = self._managers[session_id]
                manager.shutdown()  # closes subscribers, then stops the loop
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/units/common/test_execution_threading.py tests/units/common/test_test_runner.py -v`
Expected: PASS.

Then the regression that matters most — JUnit output must still be finalized for a CLI batch run, since responsibility for closing subscribers moved:

Run: `poetry run pytest tests/ -v -k "junit"`
Expected: PASS. If a test fails on a doubly-closed handler, make `JUnitEventHandler.close` idempotent (guard on an `_closed` flag), add a test asserting a second `close()` is a no-op, and include it in this commit.

- [ ] **Step 5: Commit**

```bash
for h in ruff bandit trailing-whitespace end-of-file-fixer; do \
  poetry run pre-commit run "$h" --files optics_framework/common/execution.py optics_framework/common/events.py \
          optics_framework/common/Junit_eventhandler.py \; done
          tests/units/common/test_execution_threading.py
git add optics_framework/common/execution.py optics_framework/common/events.py \
        optics_framework/common/Junit_eventhandler.py \
        tests/units/common/test_execution_threading.py
git commit -m "fix(events): own EventManager lifecycle per session, not per request"
```

---

### Task 7: Make `--workers > 1` fail loudly (finding `G8`)

`--workers` is exposed (`optics_framework/helper/cli.py:123-125`) and passed to `uvicorn.run` (`optics_framework/helper/serve.py:96-104`) with the app as an import string, so **each worker process imports `expose_api` and gets its own module-global `SessionManager`** (`optics_framework/common/expose_api.py:38`) and its own event-manager registry. A client creates a session on worker 1; the next request round-robins to worker 3; `get_session` returns `None`; the client gets a **404 on a session that demonstrably exists**, roughly 1-in-N of the time. Worker 1 meanwhile holds a live Appium session with no way to reach or terminate it.

Until the Phase 6 Redis-backed store makes this correct, it must refuse rather than silently corrupt.

**Files:**
- Modify: `optics_framework/helper/serve.py:96-104`
- Modify: `optics_framework/helper/cli.py:123-125` (help text only)
- Test: `tests/units/helper/test_serve.py` (create if absent)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `run_server(...)` raises `OpticsError(Code.E0501, ...)` when `workers > 1`. Keep the existing signature.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from optics_framework.common.error import Code, OpticsError
from optics_framework.helper import serve


def test_multiple_workers_is_refused():
    """G8: each worker has its own in-memory SessionManager, so >1 worker breaks lookups."""
    with pytest.raises(OpticsError) as exc:
        serve.run_server(host="127.0.0.1", port=8000, workers=4)
    assert exc.value.code == Code.E0501
    assert "workers" in str(exc.value).lower()


def test_single_worker_is_allowed(monkeypatch):
    called = {}
    monkeypatch.setattr(serve.uvicorn, "run", lambda *a, **kw: called.update(kw))
    serve.run_server(host="127.0.0.1", port=8000, workers=1)
    assert called["workers"] == 1
```

Match `run_server`'s real name and parameters — read `optics_framework/helper/serve.py` and adjust the calls if it differs.

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/units/helper/test_serve.py -v`
Expected: FAIL — `DID NOT RAISE OpticsError`, because `workers=4` is passed straight through to uvicorn.

- [ ] **Step 3: Write minimal implementation**

At the top of `run_server`, before the `uvicorn.run` call:

```python
    if workers > 1:
        raise OpticsError(
            Code.E0501,
            message=(
                f"--workers={workers} is not supported: each worker process holds its own "
                "in-memory session registry, so a session created on one worker is invisible "
                "to the others and returns 404. Run a single worker, or scale by running "
                "multiple single-worker instances."
            ),
        )
```

Import `OpticsError` and `Code` from `optics_framework.common.error` if not already imported.

Update the argparse help text in `optics_framework/helper/cli.py:123-125`:

```python
        parser.add_argument(
            "--workers", type=int, default=1,
            help="Number of worker processes (default: 1). Values >1 are currently "
                 "rejected: sessions are held in per-process memory.",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/units/helper/test_serve.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
for h in ruff bandit trailing-whitespace end-of-file-fixer; do \
  poetry run pre-commit run "$h" --files optics_framework/helper/serve.py optics_framework/helper/cli.py \
          tests/units/helper/test_serve.py; done
git add optics_framework/helper/serve.py optics_framework/helper/cli.py \
        tests/units/helper/test_serve.py
git commit -m "fix(serve): reject --workers>1 instead of silently losing sessions"
```

---

### Task 8: Cheap hardening and dead code (findings `I2`, `I4`, `B2` partial)

Three small, unrelated-but-adjacent fixes. Grouped because each is a few lines and none warrants its own review cycle.

**`I2`** — `allow_origins=["*"]` together with `allow_credentials=True` (`optics_framework/common/expose_api.py:105-111`) makes starlette echo the request `Origin`, which effectively permits **any** website to make credentialed cross-origin calls. With the default bind of `127.0.0.1:8000`, any page the operator visits can enumerate and drive local sessions. The two settings are mutually exclusive by design; drop the credentials flag.

**`I4`** — `internal_logger.info("Created session %s with config: %s", session_id, config.model_dump())` (`:523-527`) logs the full capabilities dict. Cloud device-farm capabilities routinely carry access keys (`browserstack.user`, `browserstack.key`, vendor tokens). `log_level` is forced to `DEBUG` (`:498`) and root is forced to `DEBUG` (`optics_framework/helper/serve.py:49`), and `SensitiveDataFormatter` is only attached to file handlers, not the console — which is where a container ships logs.

**`B2` partial** — `_executor = ThreadPoolExecutor(max_workers=1)` (`optics_framework/common/async_utils.py:13`) is created at import and never referenced. Deleting it is the only part of `B2` in scope for Phase 0; the global loop and the hard-coded 15s timeout are Phase 1.

**Files:**
- Modify: `optics_framework/common/expose_api.py:105-111`, `:523-527`
- Modify: `optics_framework/common/async_utils.py:3`, `:13`
- Test: `tests/units/common/test_expose_api.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: no new symbols. `optics_framework.common.async_utils._executor` is **removed** — verify nothing imports it.

- [ ] **Step 1: Write the failing tests**

```python
def test_cors_does_not_allow_credentials_with_wildcard_origin():
    """I2: wildcard origin + credentials lets any site drive local sessions."""
    cors = [
        m for m in expose_api.app.user_middleware
        if "CORSMiddleware" in str(m.cls)
    ]
    assert cors, "CORS middleware not found"
    options = cors[0].kwargs
    if "*" in options.get("allow_origins", []):
        assert not options.get("allow_credentials", False), (
            "allow_credentials must be False when allow_origins is a wildcard"
        )


def test_create_session_does_not_log_raw_capabilities():
    """I4: capabilities carry cloud-farm access keys; never log them verbatim."""
    mgr = MagicMock()
    mgr.create_session = MagicMock(return_value="sess-1")

    config = expose_api.SessionConfig(
        driver_sources=[{
            "appium": {
                "enabled": True,
                "url": "http://127.0.0.1:4723",
                "capabilities": {"browserstack.key": "SUPERSECRET123"},
            }
        }],
        elements_sources=[], text_detection=[], image_detection=[],
    )

    logged: list[str] = []

    def _record(msg, *args, **kwargs):
        logged.append(msg % args if args else str(msg))

    with patch.object(expose_api, "session_manager", mgr), \
         patch.object(expose_api, "execute_keyword", AsyncMock(return_value=MagicMock(data={}))), \
         patch.object(expose_api, "reconfigure_logging", MagicMock()), \
         patch.object(expose_api.internal_logger, "info", _record):
        _run(expose_api.create_session(config))

    assert not any("SUPERSECRET123" in line for line in logged), (
        f"secret leaked into logs: {logged}"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/units/common/test_expose_api.py -v -k "cors_does_not or does_not_log_raw"`
Expected: FAIL. First: `allow_credentials must be False when allow_origins is a wildcard`. Second: `secret leaked into logs: [...SUPERSECRET123...]`.

- [ ] **Step 3: Write minimal implementation**

CORS (`:105-111`):

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # allow_credentials must stay False while allow_origins is a wildcard:
    # starlette would otherwise echo the request Origin, letting any site make
    # credentialed calls against a locally-bound server.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

Session-creation log line (`:523-527`) — log the shape, not the values:

```python
        internal_logger.info(
            "Created session %s with driver_sources=%s elements_sources=%s "
            "text_detection=%s image_detection=%s project_path=%s",
            session_id,
            [next(iter(s)) for s in driver_sources if s],
            [next(iter(s)) for s in elements_sources if s],
            [next(iter(s)) for s in text_detection if s],
            [next(iter(s)) for s in image_detection if s],
            config.project_path,
        )
```

This logs engine names only, never values. `SessionConfig.normalize_sources` (`optics_framework/common/expose_api.py:297`) is typed `Dict[str, List[Dict[str, DependencyConfig]]]` and each list item is a **single-key** dict built by `_make_dependency_entry` (`:221`), so `next(iter(s))` is exactly the engine name — no other shape is possible here. The four local variables `driver_sources`, `elements_sources`, `text_detection`, `image_detection` are already in scope at this point in `create_session`.

`async_utils.py` — delete line `13` (`_executor = ...`) and drop `ThreadPoolExecutor` from the import on line `3`, keeping `TimeoutError as FutureTimeoutError` which **is** used at `:60`:

```python
from concurrent.futures import TimeoutError as FutureTimeoutError
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/units/common/test_expose_api.py -v`
Expected: PASS.

Confirm the deleted symbol has no consumers:

Run: `grep -rn "_executor" optics_framework/ tests/`
Expected: no hits in `async_utils.py`. Any other hit belongs to unrelated code — check before assuming.

Run: `poetry run pytest tests/ -q`
Expected: PASS. This is the last task in the phase, so the whole suite must be green here.

- [ ] **Step 5: Commit**

```bash
for h in ruff bandit trailing-whitespace end-of-file-fixer; do \
  poetry run pre-commit run "$h" --files optics_framework/common/expose_api.py optics_framework/common/async_utils.py \
          tests/units/common/test_expose_api.py; done
git add optics_framework/common/expose_api.py optics_framework/common/async_utils.py \
        tests/units/common/test_expose_api.py
git commit -m "fix(serve): stop logging raw capabilities and disallow credentialed wildcard CORS"
```

---

## Phase exit criteria

- [ ] `poetry run pytest tests/ -q` green.
- [ ] `grep -rn "request_template_overrides" optics_framework/` shows no `Session` field.
- [ ] `grep -rn "_drain_events_and_shutdown" optics_framework/ tests/` returns nothing.
- [ ] Eight commits on `feat/parallel-sessions-stateless-serve`, each independently green.
- [ ] Handoff note lists which pre-commit hooks were skipped and why (the `gitleaks` / `commitizen` cert issue).

## Deliberately not in this phase

`A1` (the batch keyword path blocking the loop), `A4`, `A5`, `B1` (`queue_event_sync` dropping every event in pytest-runner mode), and the rest of `B2` are Phase 1. `A2` is closed here only because it shares three lines with `G1`. Logging (`C1`–`C9`) is Phase 2; disk and factories (`E*`, `D3`) are Phase 3. `G3`/`G4`/`G5` (TTL, reaper, lifespan, caps) land in Phase 4 with the session store, because they need somewhere to record `last_accessed`.

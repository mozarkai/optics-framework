import base64
import binascii
import os
import re
import shutil
import tempfile
import time
import uuid
import asyncio
from abc import ABC, abstractmethod
from typing import Dict, Iterable, Optional
from pathlib import Path
from optics_framework.common.Junit_eventhandler import setup_junit, cleanup_junit
from optics_framework.common.config_handler import Config, ConfigHandler
from optics_framework.common.optics_builder import OpticsBuilder
from optics_framework.common.models import (
    TestCaseNode,
    ElementData,
    ApiData,
    ModuleData,
    TemplateData,
    ErrorDefinitions,
    DriverBinding,
    SessionState,
    SessionStatus,
)
from optics_framework.common.eventSDK import EventSDK
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.events import get_event_manager_registry
from optics_framework.common.logging_config import internal_logger

DEFAULT_LEASE_TTL_S = 300.0


def _to_dict_list(configs: list) -> list:
    """Convert list of config item dicts to dicts, using model_dump() where available."""
    result = []
    for item in configs:
        new_item = {}
        for name, details in item.items():
            new_item[name] = details.model_dump() if hasattr(details, "model_dump") else details
        result.append(new_item)
    return result


def _get_enabled_config_list(config: object, attr_name: str) -> list:
    """Return enabled config items for the given attribute as a list of dicts."""
    all_configs = getattr(config, attr_name, [])
    enabled = [
        item for item in all_configs
        for _name, details in item.items()
        if details.enabled
    ]
    return _to_dict_list(enabled)


def _maybe_setup_junit(
    config: Config, session_id: str, execution_output_path: Optional[str]
) -> None:
    """Configure json_path and call setup_junit when json_log and output path are set."""
    if not (config.json_log is True and execution_output_path is not None):
        return
    config.json_path = (
        str(Path(config.json_path).expanduser())
        if config.json_path
        else str((Path(execution_output_path) / "logs.json").expanduser())
    )
    setup_junit(session_id, config)


def resolve_driver_binding(config: Config) -> DriverBinding:
    """
    The single seam through which driver-endpoint selection flows
    (stateless design §7). Layer 1 reads the first enabled driver source
    from config; Layer 3 replaces this body with a DeviceRegistry lookup.
    Do not read driver endpoints from config at scattered attach sites.
    """
    for item in getattr(config, "driver_sources", []) or []:
        for name, details in item.items():
            if not getattr(details, "enabled", False):
                continue
            capabilities = getattr(details, "capabilities", None) or {}
            return DriverBinding(
                driver_type=name,
                endpoint=getattr(details, "url", None) or "",
                capabilities=dict(capabilities),
                device_id=capabilities.get("udid") or capabilities.get("appium:udid"),
            )
    raise OpticsError(Code.E0501, message="No enabled drivers found in configuration")


class SessionOwnedElsewhere(Exception):
    """The session's lease is held by another instance (never fires with the
    in-memory store; enforced once a distributed store lands in Layer 2)."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        super().__init__(f"Session {session_id} is owned by another instance")


class SessionStore(ABC):
    """Durable-truth store for SessionState, keyed by session_id (design §4).

    Lease methods are part of the interface from day one so call sites do not
    change when a distributed store (Layer 2) replaces the in-memory one.
    """

    @abstractmethod
    def put_state(self, state: SessionState) -> None: ...

    @abstractmethod
    def get_state(self, session_id: str) -> Optional[SessionState]: ...

    @abstractmethod
    def delete_state(self, session_id: str) -> None: ...

    @abstractmethod
    def list_states(self) -> Iterable[SessionState]: ...

    @abstractmethod
    def acquire_lease(self, session_id: str, instance_id: str, ttl: float) -> bool: ...

    @abstractmethod
    def renew_lease(self, session_id: str, instance_id: str, ttl: float) -> bool: ...

    @abstractmethod
    def release_lease(self, session_id: str, instance_id: str) -> None: ...


def build_session_store_from_env() -> SessionStore:
    """Select the session store from ``OPTICS_SESSION_STORE`` (design §233).

    ``memory`` (default) → :class:`InMemorySessionStore` (single process).
    ``redis`` → :class:`RedisSessionStore` from ``OPTICS_REDIS_URL`` — the
    Layer-2 multi-worker/multi-pod backend. The Redis store is imported lazily
    so the default path never requires the optional ``redis`` package.
    """
    backend = os.getenv("OPTICS_SESSION_STORE", "memory").strip().lower()
    if backend in ("redis", "rediss"):
        from optics_framework.common.session_store_redis import RedisSessionStore

        url = os.getenv("OPTICS_REDIS_URL", "redis://localhost:6379/0")
        internal_logger.info("Using RedisSessionStore for session state (url=%s)", url)
        return RedisSessionStore.from_url(url)
    if backend not in ("memory", "inmemory", "in_memory", ""):
        internal_logger.warning(
            "Unknown OPTICS_SESSION_STORE=%r; falling back to in-memory store", backend
        )
    return InMemorySessionStore()


class InMemorySessionStore(SessionStore):
    """Layer-1 store: a process-local dict; leases are always granted."""

    def __init__(self) -> None:
        self._states: Dict[str, SessionState] = {}

    def put_state(self, state: SessionState) -> None:
        self._states[state.session_id] = state

    def get_state(self, session_id: str) -> Optional[SessionState]:
        return self._states.get(session_id)

    def delete_state(self, session_id: str) -> None:
        self._states.pop(session_id, None)

    def list_states(self) -> Iterable[SessionState]:
        return list(self._states.values())

    def acquire_lease(self, session_id: str, instance_id: str, ttl: float) -> bool:
        state = self._states.get(session_id)
        if state is not None:
            state.owner_instance_id = instance_id
            state.lease_expires_at = time.time() + ttl
        return True

    def renew_lease(self, session_id: str, instance_id: str, ttl: float) -> bool:
        return self.acquire_lease(session_id, instance_id, ttl)

    def release_lease(self, session_id: str, instance_id: str) -> None:
        state = self._states.get(session_id)
        if state is not None and state.owner_instance_id == instance_id:
            state.owner_instance_id = None
            state.lease_expires_at = None


class SessionHandler(ABC):
    """Abstract interface for session management."""
    @abstractmethod
    def create_session(self, config: Config,
                       test_cases: Optional[TestCaseNode],
                       modules: Optional[ModuleData],
                       elements: Optional[ElementData],
                       apis: Optional[ApiData],
                       templates: Optional[TemplateData] = None,
                       error_definitions: Optional[ErrorDefinitions] = None) -> str:
        pass

    @abstractmethod
    def get_session(self, session_id: str) -> Optional["Session"]:
        pass

    @abstractmethod
    def terminate_session(self, session_id: str) -> None:
        pass


class SessionTemplateResolver:
    """
    Resolves template names to filesystem paths using request overrides,
    session uploads, and project templates. Used by image detection so that
    execute requests can supply template images inline or via upload.
    """

    def __init__(self, session: "Session"):
        self._session = session

    def get_template_path(self, name: str) -> Optional[str]:
        """Return path for a template name; checks request overrides, then inline, then project."""
        overrides = getattr(self._session, "request_template_overrides", None) or {}
        path = overrides.get(name)
        if path is not None:
            return path
        inline = getattr(self._session, "inline_templates", None) or {}
        path = inline.get(name)
        if path is not None:
            return path
        if self._session.templates is not None:
            return self._session.templates.get_template_path(name)
        return None


class SessionRuntime:
    """Non-serializable, process-local handles for a live session (design §3).

    Exists only in the process that currently owns the session; dropped on
    detach and rebuilt from SessionState on rehydration.
    """

    def __init__(self) -> None:
        self.config_handler: Optional[ConfigHandler] = None
        self.config: Optional[Config] = None
        self.event_sdk: Optional[EventSDK] = None
        self.optics: Optional[OpticsBuilder] = None
        self.driver = None
        self.event_queue: Optional[asyncio.Queue] = None
        self.inline_templates_dir: Optional[str] = None
        self.open_streams: int = 0


class Session:
    """A live session: durable ``state`` plus process-local ``runtime``."""

    def __init__(self, session_id: str, config: Config,
                 test_cases: Optional[TestCaseNode],
                 modules: Optional[ModuleData],
                 elements: Optional[ElementData],
                 apis: Optional[ApiData],
                 templates: Optional[TemplateData] = None,
                 error_definitions: Optional[ErrorDefinitions] = None,
                 state: Optional[SessionState] = None):
        self.session_id = session_id
        runtime = SessionRuntime()
        self.runtime: Optional[SessionRuntime] = runtime
        runtime.config_handler = ConfigHandler(config)
        runtime.config = runtime.config_handler.config
        self.test_cases = test_cases
        self.modules = modules
        self.elements = elements
        self.apis = apis
        self.templates = templates
        self.error_definitions = error_definitions
        self.request_template_overrides: Dict[str, str] = {}
        self.inline_templates: Dict[str, str] = {}
        runtime.inline_templates_dir = tempfile.mkdtemp(prefix="optics_session_")
        self._template_resolver = SessionTemplateResolver(self)

        enabled_driver_configs = _get_enabled_config_list(self.config, "driver_sources")
        enabled_element_configs = _get_enabled_config_list(self.config, "elements_sources")
        enabled_text_configs = _get_enabled_config_list(self.config, "text_detection")
        enabled_image_configs = _get_enabled_config_list(self.config, "image_detection")
        enabled_llm_configs = _get_enabled_config_list(self.config, "llm_models")

        if not enabled_driver_configs:
            raise OpticsError(Code.E0501, message="No enabled drivers found in configuration")

        runtime.event_sdk = EventSDK(runtime.config_handler)
        runtime.optics = OpticsBuilder(self)
        self.optics.add_driver(enabled_driver_configs)
        self.optics.add_element_source(enabled_element_configs)
        self.optics.add_text_detection(enabled_text_configs)
        self.optics.add_image_detection(
            enabled_image_configs, self.config.project_path or "", self._template_resolver
        )
        self.optics.add_llm(enabled_llm_configs)
        _maybe_setup_junit(config, self.session_id, self.config.execution_output_path)

        runtime.driver = self.optics.get_driver()
        runtime.event_queue = asyncio.Queue()
        self.state: SessionState = state if state is not None else self._build_initial_state()

    def _build_initial_state(self) -> SessionState:
        now = time.time()
        return SessionState(
            session_id=self.session_id,
            config=self.config.model_dump(),
            apis=self.apis,
            driver_binding=resolve_driver_binding(self.config),
            status=SessionStatus.CREATING,
            created_at=now,
            updated_at=now,
        )

    def _require_runtime(self) -> SessionRuntime:
        if self.runtime is None:
            raise OpticsError(
                Code.E0702,
                message=f"Session {self.session_id} has no live runtime (detached)",
            )
        return self.runtime

    # Runtime-handle accessors: these exist only in the owning process and are
    # rebuilt from SessionState on rehydration.
    @property
    def config_handler(self) -> ConfigHandler:
        return self._require_runtime().config_handler

    @property
    def config(self) -> Config:
        return self._require_runtime().config

    @property
    def event_sdk(self) -> EventSDK:
        return self._require_runtime().event_sdk

    @property
    def optics(self) -> OpticsBuilder:
        return self._require_runtime().optics

    @property
    def driver(self):
        return self._require_runtime().driver

    @property
    def event_queue(self) -> asyncio.Queue:
        return self._require_runtime().event_queue

    @property
    def _inline_templates_dir(self) -> Optional[str]:
        return self._require_runtime().inline_templates_dir


def _safe_template_stem(name: str) -> str:
    """Sanitize a template name into a safe filename stem (no path segments)."""
    stem = re.sub(r"[^a-zA-Z0-9_.-]", "_", name).strip("._")
    if not stem or stem in (".", ".."):
        raise ValueError(f"Template name is invalid or reserved: {name!r}")
    return stem[:200]


class SessionManager(SessionHandler):
    """Local runtime cache + rehydrator over a SessionStore (design §5).

    Live ``Session`` objects in ``sessions`` are a disposable cache; the
    store's ``SessionState`` is the durable truth a session is rebuilt from.
    """

    def __init__(self, store: Optional[SessionStore] = None,
                 lease_ttl_s: float = DEFAULT_LEASE_TTL_S):
        self.instance_id = str(uuid.uuid4())
        self.store: SessionStore = store or InMemorySessionStore()
        self.lease_ttl_s = lease_ttl_s
        self.sessions: Dict[str, Session] = {}

    def create_session(self, config: Config,
                       test_cases: Optional[TestCaseNode],
                       modules: Optional[ModuleData],
                       elements: Optional[ElementData],
                       apis: Optional[ApiData],
                       templates: Optional[TemplateData] = None,
                       error_definitions: Optional[ErrorDefinitions] = None) -> str:
        """Creates a new session with a unique ID."""
        session_id = str(uuid.uuid4())
        session = Session(session_id, config, test_cases, modules, elements, apis, templates, error_definitions)
        self._sync_driver_binding(session)
        session.state.status = SessionStatus.ACTIVE
        session.state.owner_instance_id = self.instance_id
        self.store.put_state(session.state)
        self.store.acquire_lease(session_id, self.instance_id, self.lease_ttl_s)
        self.sessions[session_id] = session
        return session_id

    def get_session(self, session_id: str) -> Optional[Session]:
        """Retrieves a session by ID, rehydrating it from the store if this
        instance does not hold the live runtime. Returns None if unknown."""
        return self.get_or_rehydrate(session_id)

    def get_or_rehydrate(self, session_id: str) -> Optional[Session]:
        """The unifying lookup path (design §5): local cache hit, else rebuild
        the runtime from stored SessionState and reattach the driver."""
        session = self.sessions.get(session_id)
        if session is not None:
            # Renewing on every lookup keeps our lease alive; if renewal fails
            # the lease expired and another instance reclaimed it (Layer 2), so
            # our local runtime is stale — surface the conflict rather than
            # serving it. Always True under the in-memory store (Layer 1).
            if not self.store.renew_lease(session_id, self.instance_id, self.lease_ttl_s):
                raise SessionOwnedElsewhere(session_id)
            return session
        state = self.store.get_state(session_id)
        if state is None or state.status == SessionStatus.TERMINATED:
            return None
        if not self.store.acquire_lease(session_id, self.instance_id, self.lease_ttl_s):
            raise SessionOwnedElsewhere(session_id)
        session = self._reconstruct_runtime(state)
        self.sessions[session_id] = session
        return session

    def export_state(self, session_id: str) -> SessionState:
        """Serializable snapshot of the session, with a fresh reattach handle
        and inline template bytes (not per-instance temp paths)."""
        session = self.get_or_rehydrate(session_id)
        if session is None:
            raise OpticsError(Code.E0702, message=f"Session not found: {session_id}")
        self._sync_driver_binding(session)
        session.state.inline_templates = self._encode_inline_templates(session)
        session.state.updated_at = time.time()
        self.store.put_state(session.state)
        return session.state.model_copy(deep=True)

    def create_session_from_state(self, state: SessionState) -> str:
        """Import a session from an exported SessionState: store it, then run
        the same rehydrate path a load balancer would exercise implicitly."""
        if state.session_id in self.sessions:
            raise OpticsError(
                Code.E0503,
                message=f"Session {state.session_id} already has a live runtime on this instance",
            )
        incoming = state.model_copy(deep=True)
        incoming.status = SessionStatus.DETACHED
        incoming.owner_instance_id = None
        incoming.busy = False
        self.store.put_state(incoming)
        try:
            session = self.get_or_rehydrate(incoming.session_id)
        except Exception:
            self.store.delete_state(incoming.session_id)
            raise
        if session is None:
            self.store.delete_state(incoming.session_id)
            raise OpticsError(Code.E0702, message=f"Failed to import session {incoming.session_id}")
        return session.session_id

    def detach_session(self, session_id: str) -> SessionState:
        """Drop the local runtime but keep the backend driver session and the
        stored SessionState alive (status → DETACHED). Migration is this plus
        a later rehydrate on another instance (design §5/§9)."""
        session = self.sessions.get(session_id)
        if session is None:
            state = self.store.get_state(session_id)
            if state is None or state.status == SessionStatus.TERMINATED:
                raise OpticsError(Code.E0702, message=f"Session not found: {session_id}")
            return state.model_copy(deep=True)  # already detached — idempotent
        state = session.state
        runtime = session.runtime
        if state.busy or (runtime is not None and runtime.open_streams > 0):
            raise OpticsError(
                Code.E0503,
                message=f"Session {session_id} is busy (in-flight execution or open stream); "
                        "only idle sessions can be detached",
            )
        self._sync_driver_binding(session)
        binding = state.driver_binding
        if not binding.migratable:
            raise OpticsError(
                Code.E0104,
                message=f"Driver '{binding.driver_type}' does not support session migration; "
                        f"session {session_id} is pinned to this instance",
            )
        state.inline_templates = self._encode_inline_templates(session)
        instance = self._find_driver_instance(session, binding.driver_type)
        if instance is not None:
            instance.detach()
        self.sessions.pop(session_id, None)
        self._teardown_local_runtime(session)
        cleanup_junit(session_id)
        get_event_manager_registry().remove_session(session_id)
        state.status = SessionStatus.DETACHED
        state.owner_instance_id = None
        state.busy = False
        state.updated_at = time.time()
        self.store.put_state(state)
        self.store.release_lease(session_id, self.instance_id)
        return state.model_copy(deep=True)

    def terminate_session(self, session_id: str) -> None:
        """Terminates a session and cleans up resources. Unlike detach, this
        ends the backend driver session and deletes the stored state."""
        session: Session | None = self.sessions.pop(session_id, None)
        if session is None:
            session = self._rehydrate_for_terminate(session_id)
        if session:
            if session.driver:
                session.driver.terminate()
            self._teardown_local_runtime(session)
        cleanup_junit(session_id)
        get_event_manager_registry().remove_session(session_id)
        self.store.release_lease(session_id, self.instance_id)
        self.store.delete_state(session_id)

    def mark_busy(self, session_id: str, busy: bool) -> None:
        """Record in-flight execution so detach/migrate can refuse (design §9)."""
        session = self.sessions.get(session_id)
        if session is None:
            return
        session.state.busy = busy
        self.persist_state(session)

    def persist_state(self, session: Session) -> None:
        """Write the session's current state back to the store."""
        session.state.updated_at = time.time()
        self.store.put_state(session.state)

    # --- internals -------------------------------------------------------

    def _reconstruct_runtime(self, state: SessionState) -> Session:
        """Rebuild a live Session from stored state and strictly reattach the
        driver: a failed reattach raises rather than silently launching a
        fresh backend session (design §5)."""
        config = Config(**state.config)
        session = Session(
            state.session_id, config,
            test_cases=None, modules=None, elements=None,
            apis=state.apis, templates=None, error_definitions=None,
            state=state,
        )
        self._restore_inline_templates(session)
        binding = state.driver_binding
        if binding.reattach_handle:
            if not binding.migratable:
                raise OpticsError(
                    Code.E0104,
                    message=f"Driver '{binding.driver_type}' does not support session migration; "
                            f"cannot reattach session {state.session_id}",
                )
            instance = self._find_driver_instance(session, binding.driver_type)
            if instance is None:
                raise OpticsError(
                    Code.E0501,
                    message=f"No driver instance of type '{binding.driver_type}' available to reattach",
                )
            instance.reattach(binding.get_reattach_params(), strict=True)
        state.status = SessionStatus.ACTIVE
        state.owner_instance_id = self.instance_id
        state.updated_at = time.time()
        self.store.put_state(state)
        return session

    def _rehydrate_for_terminate(self, session_id: str) -> Optional[Session]:
        """Best-effort reattach so terminating a detached session can still end
        the backend driver session."""
        state = self.store.get_state(session_id)
        if not (state is not None
                and state.status == SessionStatus.DETACHED
                and state.driver_binding.migratable
                and state.driver_binding.reattach_handle):
            return None
        try:
            return self._reconstruct_runtime(state)
        except Exception as e:
            internal_logger.warning(
                "Could not reattach session %s to terminate its backend session: %s",
                session_id, e,
            )
            return None

    def _sync_driver_binding(self, session: Session) -> None:
        """Refresh the DriverBinding from what the driver declares through the
        capability gate (design §6). Never inspects the backend directly."""
        binding = session.state.driver_binding
        instance = self._find_driver_instance(session, binding.driver_type)
        if instance is None:
            return
        binding.migratable = bool(getattr(instance, "supports_session_migration", False))
        if not binding.migratable:
            return
        try:
            params = instance.get_reattach_params()
        except NotImplementedError:
            binding.migratable = False
            return
        binding.reattach_handle = params.get("reattach_handle")
        if params.get("endpoint"):
            binding.endpoint = params["endpoint"]
        if params.get("capabilities"):
            binding.capabilities = dict(params["capabilities"])
        if params.get("device_id"):
            binding.device_id = params["device_id"]

    def _find_driver_instance(self, session: Session, driver_type: str):
        """Locate the concrete driver instance matching the binding within the
        session's InstanceFallback (fallback level 3)."""
        instances = getattr(session.driver, "instances", None) or []
        for instance in instances:
            if getattr(instance, "NAME", type(instance).__name__.lower()) == driver_type:
                return instance
        return instances[0] if instances else None

    def _encode_inline_templates(self, session: Session) -> Dict[str, str]:
        """Read session-uploaded template files into base64 — temp paths do not
        survive a move (design §3)."""
        encoded: Dict[str, str] = {}
        for name, path in (session.inline_templates or {}).items():
            try:
                with open(path, "rb") as f:
                    encoded[name] = base64.b64encode(f.read()).decode("ascii")
            except OSError as e:
                internal_logger.warning(
                    "Skipping inline template %r during export; unreadable at %s: %s",
                    name, path, e,
                )
        return encoded

    def _restore_inline_templates(self, session: Session) -> None:
        """Rewrite inline template bytes from state into a fresh local temp dir."""
        base_dir = session._inline_templates_dir
        if not base_dir:
            return
        for name, b64 in (session.state.inline_templates or {}).items():
            try:
                raw = base64.b64decode(b64)
                path = os.path.join(base_dir, f"{_safe_template_stem(name)}.png")
            except (binascii.Error, ValueError) as e:
                internal_logger.warning("Skipping inline template %r during rehydration: %s", name, e)
                continue
            with open(path, "wb") as f:
                f.write(raw)
            session.inline_templates[name] = path

    def _teardown_local_runtime(self, session: Session) -> None:
        """Release instance-local resources without touching the backend
        driver session (the caller decides detach vs terminate)."""
        session.inline_templates.clear()
        runtime = getattr(session, "runtime", None)
        if runtime is not None:
            base_dir = runtime.inline_templates_dir
        else:
            try:
                base_dir = getattr(session, "_inline_templates_dir", None)
            except OpticsError:
                base_dir = None
        if base_dir:
            try:
                shutil.rmtree(base_dir)
            except OSError as e:
                internal_logger.warning("Failed to remove inline templates directory %s: %s", base_dir, e)
        session.runtime = None

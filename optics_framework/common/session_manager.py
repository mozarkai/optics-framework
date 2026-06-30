import shutil
import tempfile
import uuid
import asyncio
from abc import ABC, abstractmethod
from typing import Dict, Optional
from pathlib import Path
from optics_framework.common.Junit_eventhandler import setup_junit, cleanup_junit
from optics_framework.common.config_handler import Config, ConfigHandler
from optics_framework.common.optics_builder import OpticsBuilder
from optics_framework.common.models import TestCaseNode, ElementData, ApiData, ModuleData, TemplateData, ErrorDefinitions
from optics_framework.common.eventSDK import EventSDK
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.events import get_event_manager_registry
from optics_framework.common.logging_config import internal_logger


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


class Session:
    """Represents a single execution session with config and optics."""

    def __init__(self, session_id: str, config: Config,
                 test_cases: Optional[TestCaseNode],
                 modules: Optional[ModuleData],
                 elements: Optional[ElementData],
                 apis: Optional[ApiData],
                 templates: Optional[TemplateData] = None,
                 error_definitions: Optional[ErrorDefinitions] = None,
                 require_driver: bool = True):
        self.session_id = session_id
        self.config_handler = ConfigHandler(config)
        self.config = self.config_handler.config
        self.test_cases = test_cases
        self.modules = modules
        self.elements = elements
        self.apis = apis
        self.templates = templates
        self.error_definitions = error_definitions
        # When False (e.g. dry-run validation), a session may be created with no
        # enabled drivers/element sources: the builder hands back empty fallbacks
        # instead of raising, so no device is ever touched. Defaults True so normal
        # execute/live/action sessions keep their fail-fast behaviour.
        self.require_driver = require_driver
        self.request_template_overrides: Dict[str, str] = {}
        self.inline_templates: Dict[str, str] = {}
        self._inline_templates_dir: str = tempfile.mkdtemp(prefix="optics_session_")
        self._template_resolver = SessionTemplateResolver(self)

        enabled_driver_configs = _get_enabled_config_list(self.config, "driver_sources")
        enabled_element_configs = _get_enabled_config_list(self.config, "elements_sources")
        enabled_text_configs = _get_enabled_config_list(self.config, "text_detection")
        enabled_image_configs = _get_enabled_config_list(self.config, "image_detection")
        enabled_llm_configs = _get_enabled_config_list(self.config, "llm_models")

        if require_driver and not enabled_driver_configs:
            raise OpticsError(Code.E0501, message="No enabled drivers found in configuration")

        self.event_sdk = EventSDK(self.config_handler)
        self.optics = OpticsBuilder(self)
        self.optics.add_driver(enabled_driver_configs)
        self.optics.add_element_source(enabled_element_configs)
        self.optics.add_text_detection(enabled_text_configs)
        self.optics.add_image_detection(
            enabled_image_configs, self.config.project_path or "", self._template_resolver
        )
        self.optics.add_llm(enabled_llm_configs)
        _maybe_setup_junit(config, self.session_id, self.config.execution_output_path)

        self.driver = self.optics.get_driver() if require_driver else None
        self.event_queue = asyncio.Queue()


class SessionManager(SessionHandler):
    """Manages sessions in memory for both local and hosted execution."""

    def __init__(self):
        self.sessions: Dict[str, Session] = {}

    def create_session(self, config: Config,
                       test_cases: Optional[TestCaseNode],
                       modules: Optional[ModuleData],
                       elements: Optional[ElementData],
                       apis: Optional[ApiData],
                       templates: Optional[TemplateData] = None,
                       error_definitions: Optional[ErrorDefinitions] = None,
                       require_driver: bool = True) -> str:
        """Creates a new session with a unique ID.

        Pass ``require_driver=False`` for device-less sessions (e.g. dry-run
        validation) so a session can be built without any enabled driver.
        """
        session_id = str(uuid.uuid4())
        self.sessions[session_id] = Session(
            session_id, config, test_cases, modules, elements, apis, templates,
            error_definitions, require_driver=require_driver,
        )
        return session_id

    def get_session(self, session_id: str) -> Optional[Session]:
        """Retrieves a session by ID, or None if not found."""
        return self.sessions.get(session_id)

    def terminate_session(self, session_id: str) -> None:
        """Terminates a session and cleans up resources."""
        session: Session | None = self.sessions.pop(session_id, None)
        if session:
            if session.driver:
                session.driver.terminate()
            session.inline_templates.clear()
            base_dir = getattr(session, "_inline_templates_dir", None)
            if base_dir:
                try:
                    shutil.rmtree(base_dir)
                except OSError as e:
                    internal_logger.warning("Failed to remove inline templates directory %s: %s", base_dir, e)
        cleanup_junit(session_id)
        get_event_manager_registry().remove_session(session_id)

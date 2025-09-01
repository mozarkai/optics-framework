import uuid
import asyncio
from abc import ABC, abstractmethod
from typing import Dict, Optional
from pathlib import Path
from optics_framework.common.Junit_eventhandler import setup_junit, cleanup_junit
from optics_framework.common.config_handler import Config, ConfigHandler
from optics_framework.common.optics_builder import OpticsBuilder
from optics_framework.common.models import TestCaseNode, ElementData, ApiData, ModuleData, TemplateData
from optics_framework.common.eventSDK import EventSDK
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.events import get_event_manager_registry



class SessionHandler(ABC):
    """Abstract interface for session management."""
    @abstractmethod
    def create_session(self, config: Config,
                       test_cases: TestCaseNode,
                       modules: ModuleData,
                       elements: ElementData,
                       apis: ApiData,
                       templates: Optional[TemplateData] = None) -> str:
        pass

    @abstractmethod
    def get_session(self, session_id: str) -> Optional["Session"]:
        pass

    @abstractmethod
    def terminate_session(self, session_id: str) -> None:
        pass


class Session:
    """Represents a single execution session with config and optics."""

    def __init__(self, session_id: str, config: Config,
                 test_cases: Optional[TestCaseNode],
                 modules: Optional[ModuleData],
                 elements: Optional[ElementData],
                 apis: Optional[ApiData],
                 templates: Optional[TemplateData] = None):
        self.session_id = session_id
        self.config_handler = ConfigHandler(config)
        self.config = self.config_handler.config
        self.test_cases = test_cases
        self.modules = modules
        self.elements = elements
        self.apis = apis
        self.templates = templates

        # Fetch full config dicts for enabled dependencies
        def to_dict_list(configs):
            result = []
            for item in configs:
                new_item = {}
                for name, details in item.items():
                    if hasattr(details, 'model_dump'):
                        new_item[name] = details.model_dump()
                    else:
                        new_item[name] = details
                result.append(new_item)
            return result

        all_driver_configs = self.config.driver_sources if hasattr(self.config, 'driver_sources') else []
        enabled_driver_configs = [item for item in all_driver_configs for name, details in item.items() if details.enabled]
        enabled_driver_configs = to_dict_list(enabled_driver_configs)

        all_element_configs = self.config.elements_sources if hasattr(self.config, 'elements_sources') else []
        enabled_element_configs = [item for item in all_element_configs for name, details in item.items() if details.enabled]
        enabled_element_configs = to_dict_list(enabled_element_configs)

        all_text_configs = self.config.text_detection if hasattr(self.config, 'text_detection') else []
        enabled_text_configs = [item for item in all_text_configs for name, details in item.items() if details.enabled]
        enabled_text_configs = to_dict_list(enabled_text_configs)

        all_image_configs = self.config.image_detection if hasattr(self.config, 'image_detection') else []
        enabled_image_configs = [item for item in all_image_configs for name, details in item.items() if details.enabled]
        enabled_image_configs = to_dict_list(enabled_image_configs)

        if not enabled_driver_configs:
            raise OpticsError(Code.E0501, message="No enabled drivers found in configuration")

        self.event_sdk = EventSDK(self.config_handler)
        self.optics = OpticsBuilder(self)
        self.optics.add_driver(enabled_driver_configs)
        self.optics.add_element_source(enabled_element_configs)
        self.optics.add_text_detection(enabled_text_configs)
        self.optics.add_image_detection(enabled_image_configs, self.config.project_path, self.templates)
        if config.json_log is True and self.config.execution_output_path is not None:
            config.json_path = str(Path(config.json_path).expanduser()) if config.json_path else str((Path(self.config.execution_output_path) / "logs.json").expanduser())
            setup_junit(self.session_id, config)

        self.driver = self.optics.get_driver()
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
                       templates: Optional[TemplateData] = None) -> str:
        """Creates a new session with a unique ID."""
        session_id = str(uuid.uuid4())
        self.sessions[session_id] = Session(session_id, config, test_cases, modules, elements, apis, templates)
        return session_id

    def get_session(self, session_id: str) -> Optional[Session]:
        """Retrieves a session by ID, or None if not found."""
        return self.sessions.get(session_id)

    def terminate_session(self, session_id: str) -> None:
        """Terminates a session and cleans up resources."""
        session: Session | None = self.sessions.pop(session_id, None)
        if session and session.driver:
            session.driver.terminate()
        cleanup_junit(session_id)
        get_event_manager_registry().remove_session(session_id)

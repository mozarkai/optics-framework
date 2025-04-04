import uuid
import asyncio
from abc import ABC, abstractmethod
from typing import Dict, Optional
from optics_framework.common.config_handler import ConfigHandler
from optics_framework.common.optics_builder import OpticsBuilder


class SessionHandler(ABC):
    """Abstract interface for session management."""
    @abstractmethod
    def create_session(self, config: dict) -> str:
        pass

    @abstractmethod
    def get_session(self, session_id: str) -> Optional["Session"]:
        pass

    @abstractmethod
    def terminate_session(self, session_id: str) -> None:
        pass


class Session:
    """Represents a single execution session with config and optics."""

    def __init__(self, session_id: str, config: dict, project_path: str):
        self.session_id = session_id
        self.config_handler = ConfigHandler.get_instance()
        self.config_handler.set_project(project_path)
        self.config_handler.load()
        self.config = self.config_handler.config

        # Fetch enabled dependency names
        driver_sources = self.config_handler.get("driver_sources", [])
        element_sources = self.config_handler.get("elements_sources", [])
        text_detection = self.config_handler.get("text_detection", [])
        image_detection = self.config_handler.get("image_detection", [])

        if not driver_sources:
            raise ValueError("No enabled drivers found in configuration")

        self.optics = OpticsBuilder()
        self.optics.add_driver(driver_sources)
        self.optics.add_element_source(element_sources)
        self.optics.add_text_detection(text_detection)
        self.optics.add_image_detection(image_detection)

        self.driver = self.optics.get_driver() if driver_sources else None
        self.event_queue = asyncio.Queue()


class SessionManager(SessionHandler):
    """Manages sessions in memory for both local and hosted execution."""

    def __init__(self):
        self.sessions: Dict[str, Session] = {}

    def create_session(self, config: dict) -> str:
        """Creates a new session with a unique ID."""
        session_id = str(uuid.uuid4())
        project_path = config.get("project_path", "")
        self.sessions[session_id] = Session(session_id, config, project_path)
        return session_id

    def get_session(self, session_id: str) -> Optional[Session]:
        """Retrieves a session by ID, or None if not found."""
        return self.sessions.get(session_id)

    def terminate_session(self, session_id: str) -> None:
        """Terminates a session and cleans up resources."""
        session = self.sessions.pop(session_id, None)
        session.driver.terminate()

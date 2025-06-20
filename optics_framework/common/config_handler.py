import os
from collections.abc import Mapping
import logging
from typing import List, Dict, Any, Optional
import yaml
from pydantic import BaseModel, Field


def deep_merge(d1: dict, d2: dict) -> dict:
    """
    Recursively merge two dictionaries, giving priority to d2.
    """
    merged = d1.copy()
    for key, value in d2.items():
        if isinstance(value, Mapping) and key in merged and isinstance(merged[key], Mapping):
            merged[key] = deep_merge(dict(merged[key]), dict(value))
        else:
            merged[key] = value
    return merged


class DependencyConfig(BaseModel):
    """Configuration for all dependency types."""
    enabled: bool
    url: Optional[str] = None
    capabilities: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        """Pydantic V2 configuration."""
        arbitrary_types_allowed = True


class Config(BaseModel):
    """Default configuration structure."""
    console: bool = True
    driver_sources: List[Dict[str, DependencyConfig]] = Field(default_factory=list)
    elements_sources: List[Dict[str, DependencyConfig]] = Field(default_factory=list)
    text_detection: List[Dict[str, DependencyConfig]] = Field(default_factory=list)
    image_detection: List[Dict[str, DependencyConfig]] = Field(default_factory=list)
    file_log: bool = False
    json_log: bool = False
    json_path: Optional[str] = None
    log_level: str = "INFO"
    log_path: Optional[str] = None
    project_path: Optional[str] = None
    execution_output_path: Optional[str] = None
    include: Optional[List[str]] = None
    exclude: Optional[List[str]] = None
    event_attributes_json: Optional[str] = None
    halt_duration: float = 0.1
    max_attempts: int = 3

    def __init__(self, **data):
        super().__init__(**data)
        if not self.driver_sources:
            self.driver_sources = [
                {"appium": DependencyConfig(enabled=False, url=None, capabilities={})},
                {"selenium": DependencyConfig(enabled=False, url=None, capabilities={})},
                {"ble": DependencyConfig(enabled=False, url=None, capabilities={})},
            ]
        if not self.elements_sources:
            self.elements_sources = [
                {"appium_find_element": DependencyConfig(enabled=False, url=None, capabilities={})},
                {"appium_page_source": DependencyConfig(enabled=False, url=None, capabilities={})},
                {"appium_screenshot": DependencyConfig(enabled=False, url=None, capabilities={})},
                {"camera_screenshot": DependencyConfig(enabled=False, url=None, capabilities={})},
                {"selenium_find_element": DependencyConfig(enabled=False, url=None, capabilities={})},
                {"selenium_screenshot": DependencyConfig(enabled=False, url=None, capabilities={})},
            ]
        if not self.text_detection:
            self.text_detection = [
                {"easyocr": DependencyConfig(enabled=False, url=None, capabilities={})},
                {"pytesseract": DependencyConfig(enabled=False, url=None, capabilities={})},
                {"google_vision": DependencyConfig(enabled=False, url=None, capabilities={})},
                {"remote_ocr": DependencyConfig(enabled=False, url=None, capabilities={})},
            ]
        if not self.image_detection:
            self.image_detection = [
                {"templatematch": DependencyConfig(enabled=False, url=None, capabilities={})},
                {"remote_oir": DependencyConfig(enabled=False, url=None, capabilities={})},
            ]

    class Config:
        """Pydantic V2 configuration."""
        arbitrary_types_allowed = True


class ConfigHandler:
    _instance = None
    _initialized = False
    DEFAULT_GLOBAL_CONFIG_PATH = os.path.expanduser("~/.optics/global_config.yaml")
    DEPENDENCY_KEYS: List[str] = [
        "driver_sources",
        "elements_sources",
        "text_detection",
        "image_detection"
    ]

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigHandler, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self.project_name: Optional[str] = None
            self.project_config_path: Optional[str] = None
            self.global_config_path: str = self.DEFAULT_GLOBAL_CONFIG_PATH
            self.config: Config = Config()
            self._enabled_configs: Dict[str, List[str]] = {}
            self._initialized = True

    def set_project(self, project_name: str) -> None:
        self.project_name = project_name
        self.project_config_path = os.path.join(project_name, "config.yaml") if project_name else None
        self.config.project_path = self.get_project_path()
        self.config.execution_output_path = self.get_execution_output_path()

    def get_project_path(self) -> Optional[str]:
        return os.path.dirname(self.project_config_path) if self.project_config_path else None

    def get_execution_output_path(self) -> Optional[str]:
        if self.project_config_path:
            return os.path.join(os.path.dirname(self.project_config_path), "execution_output")
        return None

    def _ensure_global_config(self) -> None:
        if not os.path.exists(self.global_config_path):
            os.makedirs(os.path.dirname(self.global_config_path), exist_ok=True)
            with open(self.global_config_path, "w", encoding="utf-8") as f:
                yaml.dump(self.config.model_dump(), f, default_flow_style=False)

    def load(self) -> Config:
        global_config = self._load_yaml(self.global_config_path) or {}
        project_config = self._load_yaml(self.project_config_path) if self.project_config_path else {}
        default_dict = self.config.model_dump()
        merged = deep_merge(default_dict, global_config)
        merged = deep_merge(merged, project_config)
        self.config = Config(**merged)
        self._precompute_enabled_configs()

        if "execution_output_path" in merged:
            self.config.execution_output_path = merged["execution_output_path"]
        return self.config

    def update_config(self, new_config: dict) -> None:
        """
        Update the current configuration with a new configuration dictionary.
        This will merge the new configuration into the existing one.
        """
        current_config_dict = self.config.model_dump()
        new_config_dict = new_config if isinstance(new_config, dict) else new_config.model_dump()
        # Merge new_config into current_dict
        merged_config = deep_merge(current_config_dict, new_config_dict)
        # Reconstruct the Config model from merged data
        self.config = Config(**merged_config)
        # Refresh the enabled config keys
        self._precompute_enabled_configs()


    def _load_yaml(self, path: str) -> dict:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except yaml.YAMLError as e:
                logging.error(f"Error parsing YAML file {path}: {e}")
                return {}
        return {}

    def _is_enabled(self, details: Any) -> bool:
        """Check if a configuration is enabled."""
        return details.enabled

    def _precompute_enabled_configs(self) -> None:
        """Precompute enabled configuration names for each dependency type."""
        for key in self.DEPENDENCY_KEYS:
            dependencies = getattr(self.config, key, [])
            self._enabled_configs[key] = [
                name for item in dependencies
                for name, details in item.items()
                if self._is_enabled(details)
            ]

    def get_dependency_config(self, dependency_type: str, name: str) -> Optional[Dict[str, Any]]:
        for item in getattr(self.config, dependency_type):
            if name in item and item[name].enabled:
                return {
                    "url": item[name].url,
                    "capabilities": item[name].capabilities
                }
        return None

    def get(self, key: str, default: Any = None) -> Any:
        if key in self.DEPENDENCY_KEYS:
            return self._enabled_configs.get(key, default if default is not None else [])
        return getattr(self.config, key, default)

    def save_config(self) -> None:
        with open(self.global_config_path, "w", encoding="utf-8") as f:
            yaml.dump(self.config.model_dump(), f, default_flow_style=False)

    @classmethod
    def get_instance(cls) -> 'ConfigHandler':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

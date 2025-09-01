import os
from collections.abc import Mapping
import logging
from typing import List, Dict, Any, Optional
import yaml
from pydantic import BaseModel, Field
from optics_framework.common.logging_config import initialize_handlers
from optics_framework.common.error import OpticsError, Code


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

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a config attribute by key, returning default if not present.
        """
        return getattr(self, key, default)

def deep_merge(c1: Config, c2: Config) -> Config:
    """
    Recursively merge two Config objects, giving priority to c2.
    """
    d1 = c1.model_dump()
    d2 = c2.model_dump()

    def _merge_dicts(d1, d2):
        merged = d1.copy()
        for key, value in d2.items():
            if (
                isinstance(value, Mapping)
                and key in merged
                and isinstance(merged[key], Mapping)
            ):
                merged[key] = _merge_dicts(dict(merged[key]), dict(value))
            else:
                merged[key] = value
        return merged

    merged_dict = _merge_dicts(d1, d2)
    return Config(**merged_dict)


class ConfigHandler:
    DEFAULT_GLOBAL_CONFIG_PATH = os.path.expanduser("~/.optics/global_config.yaml")
    DEPENDENCY_KEYS: List[str] = [
        "driver_sources",
        "elements_sources",
        "text_detection",
        "image_detection"
    ]

    def __init__(self, config: Optional[Config] = None):
        self.project_name: Optional[str] = None
        self.global_config_path: str = self.DEFAULT_GLOBAL_CONFIG_PATH
        if config is None:
            raise OpticsError(Code.E0501, message="ConfigHandler requires a Config object on initialization.")
        if config.execution_output_path is None and config.project_path is not None:
            config.execution_output_path = os.path.join(
                config.project_path, "execution_output"
            )
            if not os.path.exists(config.execution_output_path):
                os.makedirs(config.execution_output_path, exist_ok=True)

        self.config: Config = config
        self._enabled_configs: Dict[str, List[str]] = {}
        self._precompute_enabled_configs()
        initialize_handlers(self.config)

    def set_project(self, project_name: str) -> None:
        self.project_name = project_name


    def _ensure_global_config(self) -> None:
        if not os.path.exists(self.global_config_path):
            os.makedirs(os.path.dirname(self.global_config_path), exist_ok=True)
            with open(self.global_config_path, "w", encoding="utf-8") as f:
                yaml.dump(self.config.model_dump(), f, default_flow_style=False)

    def load(self) -> Config:
        default_config = Config()
        global_config = self._load_yaml(self.global_config_path)
        if global_config is None:
            global_config = Config()
        project_config = self.config
        merged = deep_merge(default_config, global_config)
        self.config = deep_merge(merged, project_config)
        self._precompute_enabled_configs()

        if hasattr(merged, "execution_output_path"):
            self.config.execution_output_path = merged.execution_output_path
        return self.config

    def update_config(self, new_config: dict) -> None:
        """
        Update the current configuration with a new configuration dictionary or Config object.
        This will merge the new configuration into the existing one.
        """
        current_config = self.config
        if isinstance(new_config, dict):
            new_config_obj = Config(**new_config)
        elif isinstance(new_config, Config):
            new_config_obj = new_config
        else:
            raise OpticsError(Code.E0503, message="new_config must be a dict or Config object")
        merged_config = deep_merge(current_config, new_config_obj)
        self.config = merged_config
        self._precompute_enabled_configs()


    def _load_yaml(self, path: str) -> Optional[Config]:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                    return Config(**data)
            except (yaml.YAMLError, IOError) as e:
                logging.error(f"Error parsing YAML file {path}: {e}")
                return None
        return None

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
        os.makedirs(os.path.dirname(self.global_config_path), exist_ok=True)
        with open(self.global_config_path, "w", encoding="utf-8") as f:
            yaml.dump(self.config.model_dump(), f, default_flow_style=False)

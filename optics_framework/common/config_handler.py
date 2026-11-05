import json
import os
import re
from collections.abc import Mapping
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from optics_framework.common.error import OpticsError, Code
from optics_framework.common.logging_config import initialize_handlers, is_secret_entry, register_config_secret

_ENV_REFERENCE = re.compile(r"\$(?:\$|\{([A-Za-z_]\w*)(?::-([^}]*))?\}|([A-Za-z_]\w*))")
_RELATIVE_PREFIXES = ("./", "../")
_OUTPUT_PATH_FIELDS = frozenset({"project_path", "execution_output_path", "log_path", "json_path"})
# Expanded by the driver at connect time, which also masks the referenced values out of its errors.
_DRIVER_EXPANDED = {"playwright": frozenset({"url", "headers"})}
_BASE_KEY = "$from"


def expand_env(value: str) -> str:
    """Replace ``$NAME``, ``${NAME}`` and ``${NAME:-default}`` with the environment; ``$$`` is a literal ``$``."""
    return _ENV_REFERENCE.sub(_env_value, value)


def env_names(value: str) -> list[str]:
    """The NAMEs of the environment references in ``value``."""
    return [braced or bare for braced, _default, bare in _ENV_REFERENCE.findall(value) if braced or bare]


def resolve_config_file(data: dict, base_dir: str) -> dict:
    """Expand environment references and resolve ``./`` paths against ``base_dir`` in a parsed config file."""
    resolved = {
        key: [_resolve_driver(entry, base_dir) for entry in value]
        if key == "driver_sources" and isinstance(value, list)
        else _resolve(value, base_dir, key not in _OUTPUT_PATH_FIELDS)
        for key, value in data.items()
    }
    _register_secret_values(resolved)
    return resolved


def _resolve_driver(entry: Any, base_dir: str) -> Any:
    if not isinstance(entry, dict):
        return _resolve(entry, base_dir, True)
    return {name: _resolve(details, base_dir, True, _DRIVER_EXPANDED.get(name, frozenset())) for name, details in entry.items()}


def _resolve(value: Any, base_dir: str, must_exist: bool, keep: frozenset[str] = frozenset()) -> Any:
    if isinstance(value, dict):
        return _resolve_map(value, base_dir, must_exist, keep)
    if isinstance(value, list):
        return [_resolve(item, base_dir, must_exist, keep) for item in value]
    if not isinstance(value, str):
        return value
    value = expand_env(value)
    if not value.startswith(_RELATIVE_PREFIXES):
        return value
    path = os.path.abspath(os.path.join(base_dir, value))
    if must_exist and not os.path.exists(path):
        raise OpticsError(Code.E0501, message=f"The config refers to {value}, but {path} does not exist.")
    return path


def _resolve_map(value: dict, base_dir: str, must_exist: bool, keep: frozenset[str]) -> dict:
    resolved = {
        key: _checked(item) if key in keep else _resolve(item, base_dir, must_exist, keep)
        for key, item in value.items()
        if key != _BASE_KEY
    }
    return {**_base_map(value[_BASE_KEY]), **resolved} if _BASE_KEY in value else resolved


def _base_map(reference: Any) -> dict:
    """The JSON object a ``$from`` value names; the keys beside ``$from`` override it."""
    try:
        base = json.loads(expand_env(reference))
    except (TypeError, json.JSONDecodeError):
        base = None
    if not isinstance(base, dict):
        named = reference if isinstance(reference, str) and env_names(reference) else "a value"
        raise OpticsError(Code.E0501, message=f"The config takes $from: {named}, but its value is not a JSON object.")
    return base


def _checked(value: Any) -> Any:
    """Raise for an unset reference in ``value``, but return it unexpanded."""
    if isinstance(value, dict):
        if _BASE_KEY in value:
            raise OpticsError(
                Code.E0501,
                message="The config takes $from in a map the driver expands at run time, such as Playwright headers. Give its keys one by one.",
            )
        for item in value.values():
            _checked(item)
    elif isinstance(value, str):
        expand_env(value)
    return value


def _env_value(reference: re.Match[str]) -> str:
    if reference.group(0) == "$$":
        return "$"
    braced, default, bare = reference.groups()
    name = braced or bare
    value = os.environ.get(name) or default
    if value is None:
        raise OpticsError(
            Code.E0501,
            message=f"The config uses {reference.group(0)}, but the {name} environment variable is not set or is empty.",
        )
    register_config_secret(name, value)
    return value


class DependencyConfig(BaseModel):
    """Configuration for all dependency types."""
    enabled: bool
    url: Optional[str] = None
    capabilities: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)


def _register_secret_values(value: Any, name: str = "") -> None:
    """Mask secret-named string values of a config file, such as ``appium:keystorePassword``, in logs and reports."""
    if isinstance(value, dict):
        for key, item in value.items():
            _register_secret_values(item, str(key))
    elif isinstance(value, list):
        for item in value:
            _register_secret_values(item, name)
    elif is_secret_entry(name, value):
        register_config_secret(name, value)


class NLAgentConfig(BaseModel):
    """Settings of the natural-language agent in ``optics live``."""
    vision: Literal["auto", "always", "never"] = "auto"
    max_steps: int = Field(default=15, ge=1)
    escalation_model: str = ""


class Config(BaseModel):
    """Default configuration structure."""
    console: bool = True
    driver_sources: List[Dict[str, DependencyConfig]] = Field(default_factory=list)
    elements_sources: List[Dict[str, DependencyConfig]] = Field(default_factory=list)
    text_detection: List[Dict[str, DependencyConfig]] = Field(default_factory=list)
    image_detection: List[Dict[str, DependencyConfig]] = Field(default_factory=list)
    llm_models: List[Dict[str, DependencyConfig]] = Field(default_factory=list)
    file_log: bool = False
    json_log: bool = False
    save_captures: bool = True
    json_path: Optional[str] = None
    log_level: str = "INFO"
    log_path: Optional[str] = None
    project_path: Optional[str] = None
    execution_output_path: Optional[str] = None
    include: Optional[List[str]] = None
    exclude: Optional[List[str]] = None
    event_attributes_json: Optional[str] = None
    halt_duration: float = 0.1
    ai_self_heal: bool = False
    strict_element_match: bool = False
    nl_agent: NLAgentConfig = Field(default_factory=NLAgentConfig)

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
        if not self.llm_models:
            self.llm_models = [
                {"gemini": DependencyConfig(enabled=False, url=None, capabilities={})},
                {"openai": DependencyConfig(enabled=False, url=None, capabilities={})},
                {"anthropic": DependencyConfig(enabled=False, url=None, capabilities={})},
            ]

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a config attribute by key, returning default if not present.
        """
        return getattr(self, key, default)


def deep_merge(c1: Config, c2: Config) -> Config:
    """
    Recursively merge two Config objects, giving priority to c2.

    Note on list-valued keys: a list in ``c2`` REPLACES the one in ``c1``
    wholesale — it is not merged item-by-item. This matters for a project's
    ``driver_sources``/``elements_sources`` etc.: a project that lists its
    sources overrides the defaults' source list rather than appending to it.
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
    """Holds a project's resolved configuration.

    Configuration is always project-specific: the runner loads a project's own
    ``config.yaml`` directly (see ``optics execute``/``optics.py``). There is no
    global config layer anymore — the old ``~/.optics/global_config.yaml`` was
    edited by ``optics config`` but never read on the execution path, so it has
    been removed."""

    DEPENDENCY_KEYS: List[str] = [
        "driver_sources",
        "elements_sources",
        "text_detection",
        "image_detection",
        "llm_models"
    ]

    def __init__(self, config: Config):
        self.project_name: Optional[str] = None
        if config.execution_output_path is None:
            if config.project_path is not None:
                config.execution_output_path = os.path.join(
                    config.project_path, "execution_output"
                )
            else:
                config.execution_output_path = os.path.join(
                    os.getcwd(), "execution_output"
                )
            os.makedirs(config.execution_output_path, exist_ok=True)

        self.config: Config = config
        self._enabled_configs: Dict[str, List[str]] = {}
        self._precompute_enabled_configs()
        initialize_handlers(self.config)

    def set_project(self, project_name: str) -> None:
        self.project_name = project_name

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

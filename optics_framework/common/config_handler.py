import json
import os
import re
from collections.abc import Mapping
from typing import Annotated, Any, Dict, List, Literal, Optional

import yaml
from pydantic import (
    AliasChoices, BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, field_validator, model_validator,
)

from optics_framework.common.error import OpticsError, Code
from optics_framework.common.logging_config import initialize_handlers, is_secret_entry, register_config_secret
from optics_framework.common.models import PopupRule, invalid_fields, require_unique_names
from optics_framework.common.permissions import Permissions

_ENV_REFERENCE = re.compile(r"\$(?:\$|\{([A-Za-z_]\w*)(?::-([^}]*))?\}|([A-Za-z_]\w*))")
_RELATIVE_PREFIXES = ("./", "../")
_OUTPUT_PATH_FIELDS = frozenset({"project_path", "execution_output_path", "log_path", "json_path"})
_VERBATIM_FIELDS = frozenset({"popup_rules"})
# Expanded by the driver at connect time, which also masks the referenced values out of its errors.
_DRIVER_EXPANDED = {"playwright": frozenset({"url", "headers"})}
_BASE_KEY = "$from"
_DEVICE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
MAX_YAML_NODES = 1_000_000


def expand_env(value: str) -> str:
    """Replace ``$NAME``, ``${NAME}`` and ``${NAME:-default}`` with the environment; ``$$`` is a literal ``$``."""
    return _ENV_REFERENCE.sub(_env_value, value)


def env_names(value: str) -> list[str]:
    """The NAMEs of the environment references in ``value``."""
    return [braced or bare for braced, _default, bare in _ENV_REFERENCE.findall(value) if braced or bare]


class ProjectYamlError(OpticsError, yaml.YAMLError):
    """A project YAML file the loader refuses; also a YAMLError, so every reader reports it as a bad file."""


def load_project_yaml(path: str) -> Any:
    """Parse a project YAML file, refusing alias graphs that would expand past ``MAX_YAML_NODES`` nodes."""
    with open(path, encoding="utf-8") as stream:
        return parse_yaml(stream, path)


def parse_yaml(source: Any, name: str) -> Any:
    """Parse YAML text or a stream with the alias limit of ``load_project_yaml``; ``name`` labels errors."""
    loader = yaml.SafeLoader(source)
    try:
        node = loader.get_single_node()
        if node is None:
            return None
        _ExpandedSize(name).count(node)
        return loader.construct_document(node)
    finally:
        loader.dispose()


class _ExpandedSize:
    """Nodes a YAML graph has once every alias is expanded, counted once per unique node."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.sizes: dict[int, int] = {}
        self.open: set[int] = set()

    def count(self, node: yaml.Node) -> int:
        known = self.sizes.get(id(node))
        if known is not None:
            return known
        if id(node) in self.open:
            raise ProjectYamlError(Code.E0501, message=f"{self.path} has a YAML alias that refers to itself. Write the value out instead.")
        self.open.add(id(node))
        size = 1 + sum(self.count(child) for child in _children(node))
        self.open.discard(id(node))
        if size > MAX_YAML_NODES:
            raise ProjectYamlError(
                Code.E0501,
                message=f"{self.path} expands to more than {MAX_YAML_NODES} values through YAML aliases. Use fewer aliases or split the file.",
            )
        self.sizes[id(node)] = size
        return size


def _children(node: yaml.Node) -> list[yaml.Node]:
    if isinstance(node, yaml.MappingNode):
        return [item for pair in node.value for item in pair]
    return node.value if isinstance(node, yaml.SequenceNode) else []


def resolve_config_file(data: dict, base_dir: str) -> dict:
    """Expand environment references and resolve ``./`` paths against ``base_dir`` in a parsed config file.

    ``element_sources`` is read as ``elements_sources``; when both are set, ``elements_sources`` wins."""
    resolved = _resolve_fields(_Resolver(base_dir), data)
    _register_secret_values(resolved)
    return resolved


def is_devices_block(value: Any) -> bool:
    """True for the shape of a ``devices`` block, a map of entries of which one at least holds ``driver_sources``."""
    return isinstance(value, dict) and any(
        isinstance(entry, DeviceEntry) or isinstance(entry, dict) and "driver_sources" in entry
        for entry in value.values()
    )


def _resolve_fields(resolver: "_Resolver", data: dict) -> dict:
    if "element_sources" in data:
        data = dict(data)
        data.setdefault("elements_sources", data.pop("element_sources"))
    return {key: _resolve_field(resolver, key, value) for key, value in data.items()}


def _resolve_field(resolver: "_Resolver", key: str, value: Any) -> Any:
    if key in _VERBATIM_FIELDS:
        return value
    if key == "devices" and is_devices_block(value):
        return {identifier: _resolve_device(resolver, entry) for identifier, entry in value.items()}
    if key == "driver_sources" and isinstance(value, list):
        return [resolver.driver(entry) for entry in value]
    return resolver.value(value, key not in _OUTPUT_PATH_FIELDS)


def _resolve_device(resolver: "_Resolver", entry: Any) -> Any:
    """A device entry resolves as the top level does; its values take environment references and are never paths."""
    if not isinstance(entry, dict):
        return entry
    resolved = _resolve_fields(resolver, {key: value for key, value in entry.items() if key != "values"})
    values = entry.get("values")
    if isinstance(values, dict):
        values = {name: expand_env(value) if isinstance(value, str) else value for name, value in values.items()}
    return {**resolved, "values": values} if "values" in entry else resolved


class _Resolver:
    """Resolves one config file; a container that YAML aliases share is resolved once and stays shared."""

    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir
        self.done: dict[tuple[int, bool, frozenset[str]], Any] = {}

    def driver(self, entry: Any) -> Any:
        if not isinstance(entry, dict):
            return self.value(entry, True)
        return {name: self.value(details, True, _DRIVER_EXPANDED.get(name, frozenset())) for name, details in entry.items()}

    def value(self, value: Any, must_exist: bool, keep: frozenset[str] = frozenset()) -> Any:
        if isinstance(value, (dict, list)):
            key = (id(value), must_exist, keep)
            if key not in self.done:
                self.done[key] = self._map(value, must_exist, keep) if isinstance(value, dict) else [
                    self.value(item, must_exist, keep) for item in value
                ]
            return self.done[key]
        if not isinstance(value, str):
            return value
        value = expand_env(value)
        if not value.startswith(_RELATIVE_PREFIXES):
            return value
        path = os.path.abspath(os.path.join(self.base_dir, value))
        if must_exist and not os.path.exists(path):
            raise OpticsError(Code.E0501, message=f"The config refers to {value}, but {path} does not exist.")
        return path

    def _map(self, value: dict, must_exist: bool, keep: frozenset[str]) -> dict:
        resolved = {
            key: _checked(item) if key in keep else self.value(item, must_exist, keep)
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


def _register_secret_values(value: Any, name: str = "", seen: set[tuple[int, str]] | None = None) -> None:
    """Mask secret-named string values of a config file, such as ``appium:keystorePassword``, in logs and reports."""
    seen = set() if seen is None else seen
    if isinstance(value, (dict, list)):
        if (id(value), name) in seen:
            return
        seen.add((id(value), name))
    if isinstance(value, dict):
        for key, item in value.items():
            _register_secret_values(item, str(key), seen)
    elif isinstance(value, list):
        for item in value:
            _register_secret_values(item, name, seen)
    elif is_secret_entry(name, value):
        register_config_secret(name, value)


class NLAgentConfig(BaseModel):
    """Settings of the natural-language agent in ``optics live``."""
    vision: Literal["auto", "always", "never"] = "auto"
    max_steps: int = Field(default=15, ge=1)
    escalation_model: str = ""


Sources = List[Dict[str, DependencyConfig]]


def _quoted_text(value: Any) -> str:
    if not isinstance(value, str):
        # YAML reads 0123 as 83 and 12:30 as 750, so a value that is not text may not be what the file shows.
        raise ValueError("put the value in quotes, or YAML does not read it as text")
    return value


class DeviceEntry(BaseModel):
    """One device of a ``devices`` block: its own stack, laid over the top-level settings, and its values."""
    driver_sources: Sources
    elements_sources: Optional[Sources] = Field(
        default=None, validation_alias=AliasChoices("elements_sources", "element_sources"))
    text_detection: Optional[Sources] = None
    image_detection: Optional[Sources] = None
    llm_models: Optional[Sources] = None
    permissions: Optional[Permissions] = None
    values: Dict[str, Annotated[str, BeforeValidator(_quoted_text)]] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _has_an_enabled_driver(self) -> "DeviceEntry":
        if not any(details.enabled for source in self.driver_sources for details in source.values()):
            raise ValueError("needs an enabled driver in its driver_sources")
        return self


class Config(BaseModel):
    """Default configuration structure."""
    console: bool = True
    driver_sources: Sources = Field(default_factory=list)
    elements_sources: Sources = Field(default_factory=list)
    text_detection: Sources = Field(default_factory=list)
    image_detection: Sources = Field(default_factory=list)
    llm_models: Sources = Field(default_factory=list)
    file_log: bool = False
    json_log: bool = False
    save_captures: bool = True
    remote_caller: bool = False
    json_path: Optional[str] = None
    log_level: str = "INFO"
    log_path: Optional[str] = None
    name: Optional[str] = None
    project_path: Optional[str] = None
    execution_output_path: Optional[str] = None
    include: Optional[List[str]] = None
    exclude: Optional[List[str]] = None
    event_attributes_json: Optional[str] = None
    halt_duration: float = 0.1
    ai_self_heal: bool = False
    strict_element_match: bool = False
    nl_agent: NLAgentConfig = Field(default_factory=NLAgentConfig)
    popup_rules: List[PopupRule] = Field(default_factory=list)
    permissions: Permissions = Field(default_factory=dict)
    devices: Dict[str, DeviceEntry] = Field(default_factory=dict)

    @field_validator("devices", mode="before")
    @classmethod
    def _devices_block_only(cls, devices: Any) -> Any:
        # Config ignores a key it does not know, and a config written before the block may hold its own ``devices``.
        return devices if is_devices_block(devices) else {}

    @field_validator("devices")
    @classmethod
    def _device_identifiers(cls, devices: Dict[str, DeviceEntry]) -> Dict[str, DeviceEntry]:
        seen: Dict[str, str] = {}
        for identifier in devices:
            if not _DEVICE_IDENTIFIER.fullmatch(identifier):
                raise ValueError(f"'{identifier}' is not a device identifier; use letters, digits, '-' and '_'")
            twin = seen.setdefault(identifier.casefold(), identifier)
            if twin != identifier:
                # Report and failure files carry the identifier, and some file systems ignore case.
                raise ValueError(f"'{twin}' and '{identifier}' differ only by case")
        return devices

    @model_validator(mode="after")
    def _no_driver_beside_devices(self) -> "Config":
        enabled = [name for source in self.driver_sources for name, details in source.items() if details.enabled]
        if self.devices and enabled:
            raise ValueError(
                f"driver_sources: {enabled[0]} is enabled beside a devices block; give each device its own driver and disable this one"
            )
        return self

    @field_validator("popup_rules")
    @classmethod
    def _unique_popup_rule_names(cls, rules: List[PopupRule]) -> List[PopupRule]:
        require_unique_names(rules, "popup_rules")
        return rules

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
                {"apple_vision": DependencyConfig(enabled=False, url=None, capabilities={})},
                {"rapidocr": DependencyConfig(enabled=False, url=None, capabilities={})},
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

    def for_device(self, identifier: str) -> "Config":
        """The config of one device of the ``devices`` block: its entry laid over the top-level settings."""
        own = {field: value for field, value in self.devices[identifier] if field != "values" and value is not None}
        # A session changes its config, so the deep copy leaves no list or mapping shared between two devices.
        return self.model_copy(update={**own, "devices": {}}).model_copy(deep=True)

    @property
    def capture_dir(self) -> Optional[str]:
        """Where screenshots, page sources and other screen captures go; None when captures are off."""
        return self.execution_output_path if self.save_captures else None

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a config attribute by key, returning default if not present.
        """
        return getattr(self, key, default)


def config_from(data: Mapping[str, Any]) -> Config:
    """``Config(**data)``; a value that is not valid raises E0501 naming its field and the value."""
    try:
        return Config(**data)
    except ValidationError as e:
        raise OpticsError(Code.E0501, message=invalid_fields(e, data)) from e


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

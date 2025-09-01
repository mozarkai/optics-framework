from typing import Type, Dict, Optional, TypeVar, Generic, Union, List, cast
from types import ModuleType
import importlib
import pkgutil
import inspect
from pydantic import BaseModel, Field
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.error import OpticsError, Code

T = TypeVar("T")
S = TypeVar("S")


class GenericFactory(Generic[T]):
    class ModuleRegistry(BaseModel, Generic[S]):
        """Tracks registered module paths and their instances."""
        module_paths: Dict[str, str] = Field(default_factory=dict)
        instances: Dict[str, S] = {}

        class Config:
            arbitrary_types_allowed = True

    _registry: ModuleRegistry[T] = ModuleRegistry()

    @classmethod
    def register_package(cls, package: str) -> None:
        """Registers all modules within the specified package."""
        internal_logger.debug(f"Registering modules in package: {package}")
        package_obj = cls._load_package(package)
        if package_obj:
            cls._register_submodules(package_obj.__path__, package)

    @classmethod
    def _load_package(cls, package: str) -> Optional[ModuleType]:
        """Loads a package, returning None if it fails."""
        try:
            return importlib.import_module(package)
        except ModuleNotFoundError as e:
            internal_logger.debug(
                f"Package '{package}' not found, skipping: {e}")
            return None

    @classmethod
    def _register_submodules(cls, package_paths, base_package: str) -> None:
        """Recursively registers all submodules in a package."""
        for _, module_name, is_pkg in pkgutil.iter_modules(package_paths):
            full_module_name = f"{base_package}.{module_name}"
            cls._registry.module_paths[module_name] = full_module_name
            internal_logger.debug(f"Registered module: {full_module_name}")
            if is_pkg:
                cls._register_subpackage(full_module_name)

    @classmethod
    def _register_subpackage(cls, full_module_name: str) -> None:
        """Registers a subpackage and its contents recursively."""
        try:
            sub_package = importlib.import_module(full_module_name)
            cls._register_submodules(sub_package.__path__, full_module_name)
        except ModuleNotFoundError as e:
            internal_logger.debug(
                f"Skipping subpackage '{full_module_name}': {e}")

    @staticmethod
    def _locate_implementation(module: ModuleType, interface: Type[T]) -> Optional[Type[T]]:
        """Locates a class in the module that implements the given interface."""
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, interface) and obj is not interface:
                return obj
        return None

    @classmethod
    def create_instance_dynamic(
        cls,
        config_dict: dict,
        interface: Type[T],
        package: str,
        extra_kwargs: dict|None = None,
    ) -> T:
        """Unified instance creation supporting config and extra dependencies (e.g., driver)."""
        if not isinstance(config_dict, dict):
            raise OpticsError(Code.E0503, message="Each item must be a dict of {name: config}")
        key = next(iter(config_dict.keys()))
        config = config_dict[key]
        name = key

        if name not in cls._registry.module_paths:
            cls._load_module(name, package)

        try:
            module_path = cls._registry.module_paths[name]
        except KeyError as exc:
            raise OpticsError(Code.E0601, message=f"Unknown module requested: '{name}' in package '{package}'") from exc

        module = importlib.import_module(module_path)
        implementation = cls._locate_implementation(module, interface)
        if not implementation:
            raise OpticsError(Code.E0603, message=f"No implementation found in '{module_path}' for {interface.__name__}")

        sig = inspect.signature(implementation.__init__)
        kwargs = {}
        if "config" in sig.parameters:
            kwargs["config"] = config
        if extra_kwargs:
            for k, v in extra_kwargs.items():
                if k in sig.parameters:
                    kwargs[k] = v
        instance = implementation(**kwargs)
        cls._registry.instances[name] = instance
        internal_logger.debug(
            f"Instantiated {implementation.__name__} from {module_path}"
        )
        return instance

    @classmethod
    def create_instance(cls, name: List[dict], interface: Type[T], package: str) -> T:
        """Creates or retrieves instances from a list of config dicts."""
        if not isinstance(name, list):
            raise OpticsError(Code.E0503, message="Name must be a list of config dicts")
        return cls._create_fallback(name, interface, package)

    @classmethod
    def _create_or_retrieve(cls, config_dict: dict, interface: Type[T], package: str) -> T:
        """Creates a new instance or retrieves a cached one for the given config dict."""
        if not isinstance(config_dict, dict):
            raise OpticsError(Code.E0503, message="Each item must be a dict of {name: config}")
        key = next(iter(config_dict.keys()))
        config = config_dict[key]
        name = key
        if name in cls._registry.instances:
            internal_logger.debug(f"Returning cached instance for: {name}")
            return cls._registry.instances[name]

        if name not in cls._registry.module_paths:
            cls._load_module(name, package)

        try:
            module_path = cls._registry.module_paths[name]
        except KeyError as exc:
            raise OpticsError(Code.E0601, message=f"Unknown module requested: '{name}' in package '{package}'") from exc

        module = importlib.import_module(module_path)
        implementation = cls._locate_implementation(module, interface)
        if not implementation:
            raise OpticsError(Code.E0603, message=f"No implementation found in '{module_path}' for {interface.__name__}")

        sig = inspect.signature(implementation.__init__)
        try:
            if 'config' in sig.parameters:
                instance = implementation(config=config)
            else:
                instance = implementation()
        except TypeError as e:
            internal_logger.warning(f"TypeError when passing config to {implementation.__name__}: {e}. Retrying without config.")
            instance = implementation()
        cls._registry.instances[name] = instance
        internal_logger.debug(
            f"Instantiated {implementation.__name__} from {module_path}")
        return instance

    @classmethod
    def _load_module(cls, name: str, package: str) -> None:
        """Loads a specific module dynamically."""
        full_module_name = f"{package}.{name}"
        try:
            importlib.import_module(full_module_name)
            cls._registry.module_paths[name] = full_module_name
            internal_logger.debug(f"Lazily loaded module: {full_module_name}")
        except ModuleNotFoundError as e:
            internal_logger.error(
                f"Failed to load module '{full_module_name}': {e}")
            raise OpticsError(Code.E0601, message=f"Module '{name}' not found in package '{package}'") from e

    @classmethod
    def _extract_names(cls, name: Union[List[Union[str, dict]], dict]) -> List[str]:
        """Extracts module names from a list or dictionary."""
        if isinstance(name, dict):
            return [k for k, v in name.items() if v]
        if not name or not isinstance(name[0], dict):
            return [str(n) for n in name]
        normalized = []
        for item in name:
            if isinstance(item, dict):
                for k, v in item.items():
                    if v:
                        normalized.append(k)
            else:
                normalized.append(str(item))
        return normalized

    @classmethod
    def _create_fallback(cls, name: List[dict], interface: Type[T], package: str) -> T:
        """Creates a fallback instance from a list of config dicts."""
        instances = []
        for config_dict in name:
            if not isinstance(config_dict, dict):
                continue
            instances.append(cls._create_or_retrieve(config_dict, interface, package))
        return cast(T, InstanceFallback(instances))

    @classmethod
    def clear_instances(cls) -> None:
        """Clears all cached instances."""
        cls._registry.instances.clear()
        internal_logger.debug("Cleared instance cache.")


class InstanceFallback(BaseModel, Generic[T]):
    """Manages fallback instances for dynamic method invocation."""
    instances: List[T] = Field(default_factory=list)
    current_instance: Optional[T] = None

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, instances: List[T], **data):
        super().__init__(instances=instances, **data)
        self.current_instance = instances[0] if instances else None

    def __getattr__(self, attr):
        def fallback_method(*args, **kwargs):
            if not self.instances:
                internal_logger.warning(
                    f"Attempted to call '{attr}' but no valid instances exist.")
                return None
            last_exception = None
            for instance in self.instances:
                self.current_instance = instance
                try:
                    method = getattr(instance, attr)
                    return method(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    internal_logger.error(
                        f"Error calling '{attr}' on {instance}: {e}")
            self.current_instance = None
            if last_exception:
                raise last_exception
            raise AttributeError(
                f"Attribute '{attr}' not found in fallback instances.")
        return fallback_method

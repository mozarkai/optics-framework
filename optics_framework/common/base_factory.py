from typing import Type, Dict, Optional, TypeVar, Generic, Union, List, cast
from types import ModuleType
import importlib
import pkgutil
import inspect
from pydantic import BaseModel, Field
from optics_framework.common.logging_config import logger, apply_logger_format_to_all

T = TypeVar("T")
S = TypeVar("S")  # New TypeVar for FactoryState


@apply_logger_format_to_all("internal")
class GenericFactory(Generic[T]):
    """
    A generic factory class for discovering and instantiating modules dynamically.
    """
    class FactoryState(BaseModel, Generic[S]):
        """Pydantic model to manage factory state."""
        modules: Dict[str, str] = Field(
            default_factory=dict)  # {name: module_path}
        instances: Dict[str, S] = Field(
            default_factory=dict)  # {name: instance}

        class Config:
            arbitrary_types_allowed = True  # Allow generic S

    _state: FactoryState[T] = FactoryState()  # Bind to T from GenericFactory

    @classmethod
    def discover(cls, package: str) -> None:
        """
        Recursively discover and register modules within a given package.
        """
        logger.debug(f"Discovering modules in package: {package}")
        package_obj = cls._import_package(package)
        if package_obj:
            cls._recursive_discover(package_obj.__path__, package)

    @classmethod
    def _import_package(cls, package: str) -> Optional[ModuleType]:
        """Attempt to import a package and return it, or None if it fails."""
        try:
            return importlib.import_module(package)
        except ModuleNotFoundError as e:
            logger.error(f"Package '{package}' not found: {e}")
            return None

    @classmethod
    def _recursive_discover(cls, package_paths, base_package: str) -> None:
        """
        Recursively discover and register all modules in subpackages.
        """
        for _, module_name, is_pkg in pkgutil.iter_modules(package_paths):
            full_module_name = f"{base_package}.{module_name}"
            cls._state.modules[module_name] = full_module_name
            logger.debug(f"Registered module: {full_module_name}")
            if is_pkg:
                cls._discover_subpackage(full_module_name)

    @classmethod
    def _discover_subpackage(cls, full_module_name: str) -> None:
        """Discover a subpackage recursively."""
        try:
            sub_package = importlib.import_module(full_module_name)
            cls._recursive_discover(sub_package.__path__, full_module_name)
        except ModuleNotFoundError as e:
            logger.error(
                f"Failed to import subpackage '{full_module_name}': {e}")

    @staticmethod
    def _find_class(module: ModuleType, interface: Type[T]) -> Optional[Type[T]]:
        """
        Find a class in the module that implements the specified interface.
        """
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, interface) and obj is not interface:
                return obj
        return None

    @classmethod
    def get(cls, name: Union[str, List[Union[str, dict]], None], interface: Type[T]) -> T:
        """
        Retrieve an instance of the requested module implementing the given interface.
        """
        cls._ensure_discovery(interface)
        if isinstance(name, (list, dict)):
            return cls._get_fallback_instance(name, interface)
        if name is None:
            raise ValueError(
                "Name cannot be None for single instance retrieval")
        return cls._get_single_instance(name, interface)

    @classmethod
    def _ensure_discovery(cls, interface: Type[T]) -> None:
        """Ensure modules have been discovered."""
        if not cls._state.modules:
            raise RuntimeError(
                f"No modules discovered for {interface.__name__}. Call `discover` first.")

    @classmethod
    def _normalize_name_list(cls, name: Union[List[Union[str, dict]], dict]) -> List[str]:
        """Convert a list or dict input into a list of module names."""
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
    def _get_fallback_instance(cls, name: Union[List[Union[str, dict]], dict], interface: Type[T]) -> T:
        """Create a fallback proxy from a list of module names."""
        name_list = cls._normalize_name_list(name)
        instances = [cls._get_single_instance(
            single_name, interface) for single_name in name_list]
        return cast(T, FallbackProxy(instances))

    @classmethod
    def _get_single_instance(cls, name: str, interface: Type[T]) -> T:
        """Retrieve or create a single instance for a module name."""
        if name in cls._state.instances:
            logger.debug(f"Returning cached instance for: {name}")
            return cls._state.instances[name]

        module_path = cls._state.modules.get(name)  # pylint: disable=no-member
        if not module_path:
            raise ValueError(f"Unknown module requested: '{name}'")

        module = importlib.import_module(module_path)
        cls_obj = cls._find_class(module, interface)
        if not cls_obj:
            raise RuntimeError(
                f"No valid class found in '{module_path}' implementing {interface.__name__}")

        instance = cls_obj()
        cls._state.instances[name] = instance
        logger.debug(
            f"Successfully instantiated {cls_obj.__name__} from {module_path}")
        return instance

    @classmethod
    def clear_cache(cls) -> None:
        """Clear cached instances."""
        cls._state.instances.clear()  # pylint: disable=no-member
        logger.debug("Cleared instance cache.")


class FallbackProxy(BaseModel, Generic[T]):
    """Pydantic-based proxy for fallback instances."""
    instances: List[T] = Field(default_factory=list)
    current_instance: Optional[T] = None

    class Config:
        arbitrary_types_allowed = True  # Allow generic T

    def __init__(self, instances: List[T], **data):
        super().__init__(instances=instances, **data)
        self.current_instance = instances[0] if instances else None

    def __getattr__(self, attr):
        def fallback_method(*args, **kwargs):
            if not self.instances:
                logger.warning(
                    f"Attempted to call '{attr}' but no valid instances exist. Passing off the call.")
                return None

            last_exception = None
            for instance in self.instances:
                self.current_instance = instance
                try:
                    method = getattr(instance, attr)
                    return method(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    logger.error(f"Error calling '{attr}' on {instance}: {e}")
            self.current_instance = None
            if last_exception:
                raise last_exception
            raise AttributeError(
                f"Attribute '{attr}' not found in fallback instances.")
        return fallback_method

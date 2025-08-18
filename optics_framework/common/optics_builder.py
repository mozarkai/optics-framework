from typing import Union, List, Dict, Optional, Type, TypeVar, Any
from pydantic import BaseModel
from optics_framework.common.eventSDK import EventSDK
from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.driver_interface import DriverInterface
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.image_interface import ImageInterface
from optics_framework.common.text_interface import TextInterface
from optics_framework.common.factories import (
    DeviceFactory,
    ElementSourceFactory,
    ImageFactory,
    TextFactory,
)

T = TypeVar("T")  # Generic type for the build method


class OpticsConfig(BaseModel):
    """Configuration for OpticsBuilder."""

    driver_config: Optional[Union[str, List[Union[str, Dict]]]] = None
    element_source_config: Optional[Union[str, List[Union[str, Dict]]]] = None
    image_config: Optional[Union[str, List[Union[str, Dict]], List[Dict[Any, Any]]]] = None
    text_config: Optional[Union[str, List[Union[str, Dict]]]] = None


class OpticsBuilder:
    """
    A builder that sets configurations and instantiates drivers for Optics Framework API classes.
    """

    def __init__(self, event_sdk: EventSDK) -> None:
        self.config: OpticsConfig = OpticsConfig()
        self._instances: Dict[str, Any] = {}
        self.event_sdk = event_sdk
        self.project_path = event_sdk.config_handler.get("project_path")

    def normalise_config(self, config: Union[str, List[Union[str, Dict[Any, Any]]], List[Dict[Any, Any]]]) -> List[Dict[Any, Any]]:
        """
        Normalize config to a list of dicts matching the expected factory input.
        - If string: convert to {string: {}}.
        - If dict: wrap in list.
        - If list: return as is (assume already normalized).
        """
        if isinstance(config, str):
            return [{config: {}}]
        elif isinstance(config, dict):
            return [config]
        elif isinstance(config, list):
            # Ensure all items are dicts: convert strings to {string: {}}
            normalized = []
            for item in config:
                if isinstance(item, str):
                    normalized.append({item: {}})
                elif isinstance(item, dict):
                    normalized.append(item)
                else:
                    raise ValueError(f"Invalid item type in config list: {type(item)}")
            return normalized
        else:
            raise ValueError("Invalid configuration type")

    # Fluent methods to set configurations
    def add_driver(self, config: Union[str, List[Union[str, Dict]]]) -> "OpticsBuilder":
        self.config.driver_config = config
        return self

    def add_element_source(
        self, config: Union[str, List[Union[str, Dict]]]
    ) -> "OpticsBuilder":
        self.config.element_source_config = config
        return self

    def add_image_detection(
        self, config: Union[str, List[Union[str, Dict]]], project_path: str
    ) -> "OpticsBuilder":
        normalized = self.normalise_config(config)
        # Inject project_path into each config dict
        for item in normalized:
            # item is a dict like {"templatematch": {...}}
            for key in item:
                if isinstance(item[key], dict):
                    item[key]["project_path"] = project_path
        self.config.image_config = normalized
        return self

    def add_text_detection(
        self, config: Union[str, List[Union[str, Dict]]]
    ) -> "OpticsBuilder":
        self.config.text_config = config
        return self

    # Instantiation methods
    def instantiate_driver(self) -> InstanceFallback[DriverInterface]:
        if not self.config.driver_config:
            raise ValueError("Driver configuration must be set")
        normalized_config = self.normalise_config(self.config.driver_config)
        driver: InstanceFallback[DriverInterface] = DeviceFactory.get_driver(
            normalized_config,
            event_sdk=self.event_sdk
        )
        self._instances["driver"] = driver
        return driver

    def instantiate_element_source(self) -> InstanceFallback[ElementSourceInterface]:
        if not self.config.element_source_config:
            raise ValueError("Element source configuration must be set")
        driver: InstanceFallback[DriverInterface] = self.get_driver()
        # Normalize config before passing to factory
        normalized_config = self.normalise_config(self.config.element_source_config)
        element_source: InstanceFallback[ElementSourceInterface] = (
            ElementSourceFactory.get_driver(normalized_config, driver)
        )
        self._instances["element_source"] = element_source
        return element_source

    def instantiate_image_detection(self) -> Optional[InstanceFallback[ImageInterface]]:
        if not self.config.image_config:
            return None
        normalized_config = self.normalise_config(self.config.image_config)
        image_detection: InstanceFallback[ImageInterface] = ImageFactory.get_driver(
            normalized_config
        )
        self._instances["image_detection"] = image_detection
        return image_detection

    def instantiate_text_detection(self) -> Optional[InstanceFallback[TextInterface]]:
        if not self.config.text_config:
            return None
        normalized_config = self.normalise_config(self.config.text_config)
        text_detection: InstanceFallback[TextInterface] = TextFactory.get_driver(
            normalized_config
        )
        self._instances["text_detection"] = text_detection
        return text_detection

    # Retrieval methods
    def get_driver(self) -> InstanceFallback[DriverInterface]:
        if "driver" not in self._instances:
            self.instantiate_driver()
        return self._instances["driver"]

    def get_element_source(self) -> InstanceFallback[ElementSourceInterface]:
        if "element_source" not in self._instances:
            self.instantiate_element_source()
        return self._instances["element_source"]

    def get_image_detection(self) -> Optional[InstanceFallback[ImageInterface]]:
        if "image_detection" not in self._instances:
            self.instantiate_image_detection()
        return self._instances.get("image_detection", None)

    def get_text_detection(self) -> Optional[InstanceFallback[TextInterface]]:
        if "text_detection" not in self._instances:
            self.instantiate_text_detection()
        return self._instances.get("text_detection", None)

    def build(self, cls: Type[T]) -> T:
        """
        Build an instance of the specified class using the stored configurations.

        :param cls: The class to instantiate (e.g., ActionKeyword, AppManagement, Verifier).
        :return: An instance of the specified class.
        :raises ValueError: If required configurations are missing for the specified class.
        """
        instance: T = cls(self)  # type: ignore
        return instance

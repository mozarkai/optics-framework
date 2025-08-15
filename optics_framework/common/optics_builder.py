from typing import Union, List, Dict, Optional, Type, TypeVar, Any
from pydantic import BaseModel
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
    image_config: Optional[Union[str, List[Union[str, Dict]]]] = None
    text_config: Optional[Union[str, List[Union[str, Dict]]]] = None


class OpticsBuilder:
    """
    A builder that sets configurations and instantiates drivers for Optics Framework API classes.
    """

    def __init__(self) -> None:
        self.config: OpticsConfig = OpticsConfig()
        self._instances: Dict[str, Any] = {}

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
        self, config: Union[str, List[Union[str, Dict]]]
    ) -> "OpticsBuilder":
        self.config.image_config = config
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
        driver: InstanceFallback[DriverInterface] = DeviceFactory.get_driver(
            self.config.driver_config
        )
        self._instances["driver"] = driver
        return driver

    def instantiate_element_source(self) -> InstanceFallback[ElementSourceInterface]:
        if not self.config.element_source_config:
            raise ValueError("Element source configuration must be set")
        driver: InstanceFallback[DriverInterface] = self.get_driver()
        # Pass driver to element source factory for dependency injection (signature will be updated soon)
        element_source: InstanceFallback[ElementSourceInterface] = (
            ElementSourceFactory.get_driver(self.config.element_source_config, driver)
        )
        self._instances["element_source"] = element_source
        return element_source

    def instantiate_image_detection(self) -> Optional[InstanceFallback[ImageInterface]]:
        if not self.config.image_config:
            return None
        image_detection: InstanceFallback[ImageInterface] = ImageFactory.get_driver(
            self.config.image_config
        )
        self._instances["image_detection"] = image_detection
        return image_detection

    def instantiate_text_detection(self) -> Optional[InstanceFallback[TextInterface]]:
        if not self.config.text_config:
            return None
        text_detection: InstanceFallback[TextInterface] = TextFactory.get_driver(
            self.config.text_config
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

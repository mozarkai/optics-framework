from typing import Union, List, Dict, Optional, Type, TypeVar
from optics_framework.common.factories import DeviceFactory, ElementSourceFactory, ImageFactory, TextFactory
from pydantic import BaseModel

T = TypeVar('T')  # Generic type for the build method


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

    def __init__(self):
        self.config = OpticsConfig()

    # Fluent methods to set configurations
    def add_driver(self, config: Union[str, List[Union[str, Dict]]]) -> 'OpticsBuilder':
        self.config.driver_config = config
        return self

    def add_element_source(self, config: Union[str, List[Union[str, Dict]]]) -> 'OpticsBuilder':
        self.config.element_source_config = config
        return self

    def add_image_detection(self, config: Union[str, List[Union[str, Dict]]]) -> 'OpticsBuilder':
        self.config.image_config = config
        return self

    def add_text_detection(self, config: Union[str, List[Union[str, Dict]]]) -> 'OpticsBuilder':
        self.config.text_config = config
        return self

    # Methods to instantiate drivers
    def get_driver(self):
        if not self.config.driver_config:
            raise ValueError("Driver configuration must be set")
        return DeviceFactory.get_driver(self.config.driver_config)

    def get_element_source(self):
        if not self.config.element_source_config:
            raise ValueError("Element source configuration must be set")
        return ElementSourceFactory.get_driver(self.config.element_source_config)

    def get_image_detection(self):
        if not self.config.image_config:
            return None
        return ImageFactory.get_driver(self.config.image_config)

    def get_text_detection(self):
        if not self.config.text_config:
            return None
        return TextFactory.get_driver(self.config.text_config)

    def build(self, cls: Type[T]) -> T:
        """
        Build an instance of the specified class using the stored configurations.

        :param cls: The class to instantiate (e.g., ActionKeyword, AppManagement, Verifier).
        :return: An instance of the specified class.
        :raises ValueError: If required configurations are missing for the specified class.
        """
        instance = cls(self)  # type: ignore
        return instance

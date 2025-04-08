from typing import Union, List
from optics_framework.common.base_factory import GenericFactory
from optics_framework.common.driver_interface import DriverInterface
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.image_interface import ImageInterface
from optics_framework.common.text_interface import TextInterface


class DeviceFactory(GenericFactory[DriverInterface]):
    DEFAULT_PACKAGE = "optics_framework.engines.drivers"

    @classmethod
    def get_driver(cls, name: Union[str, List[Union[str, dict]], None]) -> DriverInterface:
        return cls.create_instance(name, DriverInterface, cls.DEFAULT_PACKAGE)


class ElementSourceFactory(GenericFactory[ElementSourceInterface]):
    DEFAULT_PACKAGE = "optics_framework.engines.elementsources"

    @classmethod
    def get_driver(cls, name: Union[str, List[Union[str, dict]], None]) -> ElementSourceInterface:
        return cls.create_instance(name, ElementSourceInterface, cls.DEFAULT_PACKAGE)


class ImageFactory(GenericFactory[ImageInterface]):
    DEFAULT_PACKAGE = "optics_framework.engines.vision_models.image_models"

    @classmethod
    def get_driver(cls, name: Union[str, List[Union[str, dict]], None]) -> ImageInterface:
        return cls.create_instance(name, ImageInterface, cls.DEFAULT_PACKAGE)


class TextFactory(GenericFactory[TextInterface]):
    DEFAULT_PACKAGE = "optics_framework.engines.vision_models.ocr_models"

    @classmethod
    def get_driver(cls, name: Union[str, List[Union[str, dict]], None]) -> TextInterface:
        return cls.create_instance(name, TextInterface, cls.DEFAULT_PACKAGE)

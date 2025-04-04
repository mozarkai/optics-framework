from typing import Union, List
from optics_framework.common.base_factory import GenericFactory
from optics_framework.common.driver_interface import DriverInterface
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.image_interface import ImageInterface
from optics_framework.common.text_interface import TextInterface

class DeviceFactory(GenericFactory[DriverInterface]):
    """
    Factory class for managing device drivers.

    This class extends :class:`GenericFactory` to handle the discovery and
    instantiation of drivers implementing the :class:`DriverInterface`.

    :Methods:
        - :meth:`discover_drivers` - Discovers available drivers in the specified package.
        - :meth:`get_driver` - Retrieves an instance of a specified driver.
    """

    @classmethod
    def discover_drivers(
        cls, package: str = "optics_framework.engines.drivers"
    ) -> None:
        """
        Discover and register all available drivers.

        :param package: The package containing driver implementations.
                        Defaults to ``"optics_framework.engines.drivers"``.
        :type package: str
        :return: None
        :rtype: None
        """
        cls.discover(package)

    @classmethod
    def get_driver(cls, name: Union[str, List[Union[str, dict]], None]) -> DriverInterface:
        """
        Retrieve an instance of the specified driver.

        :param name: The name of the driver to retrieve.
        :type name: str or List[str]
        :return: An instance of the requested driver.
        :rtype: DriverInterface
        :raises ValueError: If the requested driver is not found.
        :raises RuntimeError: If no valid class implementing :class:`DriverInterface` is found.
        """
        return cls.get(name, DriverInterface)


class ElementSourceFactory(GenericFactory[ElementSourceInterface]):
    """
    Factory class for managing element sources.

    This class extends :class:`GenericFactory` to handle the discovery and
    instantiation of element sources implementing the :class:`ElementSourceInterface`.

    :Methods:
        - :meth:`discover_elementsources` - Discovers available element sources in the specified package.
        - :meth:`get_elementsource` - Retrieves an instance of a specified element source.
    """

    @classmethod
    def discover_elementsources(
        cls, package: str = "optics_framework.engines.elementsources"
    ) -> None:
        """
        Discover and register all available element sources.

        :param package: The package containing element source implementations.
                Defaults to ``"optics_framework.engines.elementsources"``.
        :type package: str
        :return: None
        :rtype: None
        """
        cls.discover(package)

    @classmethod
    def get_driver(cls, name: Union[str, List[Union[str, dict]], None]) -> ElementSourceInterface:
        """
        Retrieve an instance of the specified element source.

        :param name: The name of the element source to retrieve.
        :type name: str
        :return: An instance of the requested element source.
        :rtype: ElementSourceInterface
        :raises ValueError: If the requested element source is not found.
        :raises RuntimeError: If no valid class implementing :class:`ElementSourceInterface` is found.
        """
        return cls.get(name, ElementSourceInterface)


class ImageFactory(GenericFactory[ImageInterface]):
    """
    Factory class for managing image processing engines.

    This class extends :class:`GenericFactory` to handle the discovery and
    instantiation of image-related detection models that implement
    :class:`ImageInterface`.

    :Methods:
        - :meth:`discover_drivers`: Discovers available image detection engines.
        - :meth:`get_driver`: Retrieves an instance of a specified image detection engine.
    """

    @classmethod
    def discover_drivers(
        cls, package: str = "optics_framework.engines.vision_models.image_models"
    ) -> None:
        """
        Discover and register available image detection engines.

        :param package: The package containing vision detection implementations.
                        Defaults to ``"optics_framework.engines.vision_models.image_models"``.
        :type package: str
        :return: None
        :rtype: None
        """
        cls.discover(package)

    @classmethod
    def get_driver(cls, name: Union[str, List[Union[str, dict]], None]) -> ImageInterface:
        """
        Retrieve an instance of the specified vision detection engine.

        :param name: The name of the vision engine to retrieve. Can be a string or a list of strings/dicts.
        :type name: Union[str, List[Union[str, dict]]]
        :return: An instance of the requested vision detection engine.
        :rtype: ImageInterface

        :raises ValueError: If the requested vision engine is not found.
        :raises RuntimeError: If no valid class implementing :class:`ImageInterface` is found.
        """
        return cls.get(name, ImageInterface)


class TextFactory(GenericFactory[TextInterface]):
    """
    Factory class for managing vision processing engines.

    This class extends :class:`GenericFactory` to handle the discovery and
    instantiation of vision-related detection models that implement
    :class:`TextInterface`.

    :Methods:
        - :meth:`discover_drivers`: Discovers available vision detection engines.
        - :meth:`get_driver`: Retrieves an instance of a specified vision detection engine.
    """

    @classmethod
    def discover_drivers(
        cls, package: str = "optics_framework.engines.vision_models.ocr_models"
    ) -> None:
        """
        Discover and register available vision detection engines.

        :param package: The package containing vision detection implementations.
                        Defaults to ``"optics_framework.engines.vision_models.ocr_models"``.
        :type package: str
        :return: None
        :rtype: None
        """
        cls.discover(package)

    @classmethod
    def get_driver(cls, name: Union[str, List[Union[str, dict]], None]) -> TextInterface:
        """
        Retrieve an instance of the specified vision detection engine.

        :param name: The name of the vision engine to retrieve. Can be a string or a list of strings/dicts.
        :type name: Union[str, List[Union[str, dict]]]
        :return: An instance of the requested vision detection engine.
        :rtype: TextInterface

        :raises ValueError: If the requested vision engine is not found.
        :raises RuntimeError: If no valid class implementing :class:`TextInterface` is found.
        """
        return cls.get(name, TextInterface)


DeviceFactory.discover_drivers()
ElementSourceFactory.discover_elementsources()
ImageFactory.discover_drivers()
TextFactory.discover_drivers()

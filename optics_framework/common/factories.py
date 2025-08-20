from typing import List
from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.base_factory import GenericFactory
from optics_framework.common.driver_interface import DriverInterface
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.image_interface import ImageInterface
from optics_framework.common.text_interface import TextInterface


class DeviceFactory(GenericFactory[DriverInterface]):
    DEFAULT_PACKAGE = "optics_framework.engines.drivers"

    @classmethod
    def get_driver(cls, name: List[dict], event_sdk=None) -> InstanceFallback[DriverInterface]:
        """
        Instantiate all driver instances, passing event_sdk to those that accept it.
        """
        instances = [
            cls.create_instance_dynamic(
                config_dict,
                DriverInterface,
                cls.DEFAULT_PACKAGE,
                extra_kwargs={"event_sdk": event_sdk} if event_sdk is not None else {}
            )
            for config_dict in name
        ]
        return InstanceFallback(instances)


class ElementSourceFactory(GenericFactory[ElementSourceInterface]):
    DEFAULT_PACKAGE = "optics_framework.engines.elementsources"

    @classmethod
    def get_driver(
        cls,
        name: List[dict],
        driver: InstanceFallback[DriverInterface]
    ) -> InstanceFallback[ElementSourceInterface]:
        driver_instances = driver.instances if hasattr(driver, "instances") else [driver]
        instances = []
        for config_dict in name:
            es_name = next(iter(config_dict.keys()))
            implementation = cls._load_element_source_implementation(es_name)
            matched_driver = cls._find_matching_driver(implementation, driver_instances)
            extra_kwargs = cls._build_driver_kwargs(implementation, matched_driver)
            instance = cls.create_instance_dynamic(config_dict, ElementSourceInterface, cls.DEFAULT_PACKAGE, extra_kwargs=extra_kwargs)
            instances.append(instance)
        return InstanceFallback(instances)

    @classmethod
    def _load_element_source_implementation(cls, es_name: str):
        """Load and return the implementation class for the given element source name."""
        if es_name not in cls._registry.module_paths:
            cls._load_module(es_name, cls.DEFAULT_PACKAGE)
        module_path = cls._registry.module_paths[es_name]
        module = __import__(module_path, fromlist=[''])
        return cls._locate_implementation(module, ElementSourceInterface)

    @classmethod
    def _find_matching_driver(cls, implementation, driver_instances):
        """Find a driver instance that matches the required driver type."""
        required_type = getattr(implementation, "REQUIRED_DRIVER_TYPE", None)
        if not required_type:
            return None

        for drv in driver_instances:
            drv_type = getattr(drv, "NAME", None)
            if drv_type == required_type:
                return drv
        return None

    @classmethod
    def _build_driver_kwargs(cls, implementation, matched_driver):
        """Build extra_kwargs dictionary for driver if required and matched."""
        required_type = getattr(implementation, "REQUIRED_DRIVER_TYPE", None)
        if required_type and matched_driver:
            return {'driver': matched_driver}
        return {}


class ImageFactory(GenericFactory[ImageInterface]):
    DEFAULT_PACKAGE = "optics_framework.engines.vision_models.image_models"

    @classmethod
    def get_driver(cls, name: List[dict]) -> InstanceFallback[ImageInterface]:
        instances = [cls.create_instance_dynamic(config_dict, ImageInterface, cls.DEFAULT_PACKAGE) for config_dict in name]
        return InstanceFallback(instances)


class TextFactory(GenericFactory[TextInterface]):
    DEFAULT_PACKAGE = "optics_framework.engines.vision_models.ocr_models"

    @classmethod
    def get_driver(cls, name: List[dict]) -> InstanceFallback[TextInterface]:
        instances = [cls.create_instance_dynamic(config_dict, TextInterface, cls.DEFAULT_PACKAGE) for config_dict in name]
        return InstanceFallback(instances)

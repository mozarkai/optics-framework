from typing import Union, List
import inspect
from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.base_factory import GenericFactory
from optics_framework.common.driver_interface import DriverInterface
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.image_interface import ImageInterface
from optics_framework.common.text_interface import TextInterface


class DeviceFactory(GenericFactory[DriverInterface]):
    DEFAULT_PACKAGE = "optics_framework.engines.drivers"

    @classmethod
    def get_driver(cls, name: Union[str, List[Union[str, dict]], None]) -> InstanceFallback[DriverInterface]:
        # Always return InstanceFallback, even for single instance
        instance = cls.create_instance(name, DriverInterface, cls.DEFAULT_PACKAGE)
        if isinstance(instance, InstanceFallback):
            return instance
        return InstanceFallback([instance])


class ElementSourceFactory(GenericFactory[ElementSourceInterface]):
    DEFAULT_PACKAGE = "optics_framework.engines.elementsources"

    @classmethod
    def get_driver(
        cls,
        name: Union[str, List[Union[str, dict]], None],
        driver: InstanceFallback[DriverInterface]
    ) -> InstanceFallback[ElementSourceInterface]:
        def normalize_name(n):
            if isinstance(n, dict):
                return next(iter(n.keys()))
            return str(n)

        # Extract driver instances from InstanceFallback
        driver_instances = driver.instances if hasattr(driver, "instances") else [driver]

        def matches_required_type(implementation, driver_instance):
            required_type = getattr(implementation, "REQUIRED_DRIVER_TYPE", None)
            driver_type = getattr(driver_instance, "NAME", None)
            return required_type is None or driver_type == required_type

        if isinstance(name, list):
            instances = []
            for idx, n in enumerate(name):
                norm_name = normalize_name(n)
                if norm_name not in cls._registry.module_paths:
                    cls._load_module(norm_name, cls.DEFAULT_PACKAGE)
                module_path = cls._registry.module_paths[norm_name]
                module = __import__(module_path, fromlist=[''])
                implementation = cls._locate_implementation(module, ElementSourceInterface)
                driver_instance = driver_instances[idx] if idx < len(driver_instances) else driver_instances[0]
                if isinstance(driver_instance, InstanceFallback):
                    driver_instance = driver_instance.instances[0]
                if matches_required_type(implementation, driver_instance):
                    instance = cls.create_instance_with_driver(norm_name, ElementSourceInterface, cls.DEFAULT_PACKAGE, driver_instance)
                    instances.append(instance)
                else:
                    continue
            # Always wrap in InstanceFallback, even if only one instance
            return InstanceFallback(instances)
        else:
            norm_name = normalize_name(name)
            if norm_name not in cls._registry.module_paths:
                cls._load_module(norm_name, cls.DEFAULT_PACKAGE)
            module_path = cls._registry.module_paths[norm_name]
            module = __import__(module_path, fromlist=[''])
            implementation = cls._locate_implementation(module, ElementSourceInterface)
            driver_instance = driver_instances[0]
            if isinstance(driver_instance, InstanceFallback):
                driver_instance = driver_instance.instances[0]
            if matches_required_type(implementation, driver_instance):
                instance = cls.create_instance_with_driver(norm_name, ElementSourceInterface, cls.DEFAULT_PACKAGE, driver_instance)
                # Always wrap in InstanceFallback, even for single instance
                return InstanceFallback([instance])
            else:
                return InstanceFallback([])

    @classmethod
    def create_instance_with_driver(
        cls,
        name: str,
        interface,
        package,
        driver: DriverInterface
    ) -> ElementSourceInterface:
        # Load module and inject driver into constructor if supported
        if name is None:
            raise ValueError("Name cannot be None for element source instance retrieval")
        if name not in cls._registry.module_paths:
            cls._load_module(name, package)
        module_path = cls._registry.module_paths[name]
        module = __import__(module_path, fromlist=[''])
        implementation = cls._locate_implementation(module, interface)
        if not implementation:
            raise RuntimeError(f"No implementation found in '{module_path}' for {interface.__name__}")
        # Check if implementation accepts driver argument
        sig = inspect.signature(implementation.__init__)
        if 'driver' in sig.parameters:
            return implementation(driver=driver)  # type: ignore
        else:
            return implementation()


class ImageFactory(GenericFactory[ImageInterface]):
    DEFAULT_PACKAGE = "optics_framework.engines.vision_models.image_models"

    @classmethod
    def get_driver(cls, name: Union[str, List[Union[str, dict]], None]) -> InstanceFallback[ImageInterface]:
        instance = cls.create_instance(name, ImageInterface, cls.DEFAULT_PACKAGE)
        if isinstance(instance, InstanceFallback):
            return instance
        return InstanceFallback([instance])


class TextFactory(GenericFactory[TextInterface]):
    DEFAULT_PACKAGE = "optics_framework.engines.vision_models.ocr_models"

    @classmethod
    def get_driver(cls, name: Union[str, List[Union[str, dict]], None]) -> InstanceFallback[TextInterface]:
        instance = cls.create_instance(name, TextInterface, cls.DEFAULT_PACKAGE)
        if isinstance(instance, InstanceFallback):
            return instance
        return InstanceFallback([instance])

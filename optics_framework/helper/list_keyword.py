import pkgutil
import inspect
import importlib
import optics_framework.api


def list_api_methods(package):
    """
    Dynamically fetch all class methods from the given package.

    : param package: The package from which to extract class methods.
    : type package: module
    : return: A dictionary where keys are class names and values are lists of method names.
    : rtype: dict[str, list[str]]
    """
    api_methods = {}

    for _, module_name, _ in pkgutil.iter_modules(package.__path__):
        module = importlib.import_module(f"{package.__name__}.{module_name}")

        for name, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ == module.__name__:  # Ensure class belongs to this module
                methods = [
                    func
                    for func in dir(cls)
                    if callable(getattr(cls, func)) and not func.startswith("_")
                ]
                api_methods[name] = methods

    return api_methods


def format_methods(api_methods):
    """
    Format extracted methods into a human-readable string.

    :param api_methods: Dictionary mapping class names to their methods.
    :type api_methods: dict[str, list[str]]
    :return: A formatted string listing all classes and their methods.
    :rtype: str
    """
    return "\n".join(
        [
            f"{class_name}:\n  - " + "\n  - ".join(methods)
            for class_name, methods in api_methods.items()
        ]
    )



def main():
    """
    CLI entry point for listing API methods.
    """
    api_methods = list_api_methods(optics_framework.api)
    print(format_methods(api_methods))


if __name__ == "__main__":
    main()

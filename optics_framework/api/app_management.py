from typing import Optional
from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.optics_builder import OpticsBuilder


class AppManagement:
    """
    A high-level API for managing applications.

    This class provides functionality for launching, terminating,
    and modifying app settings.

    Attributes:
        driver (object): The driver instance for managing applications.
    """

    def __init__(self, builder: OpticsBuilder):
        self.driver: InstanceFallback = builder.get_driver()
        if self.driver is None:
            internal_logger.error("Driver could not be initialized due to not being provided.")
            # Optionally raise an exception if this should halt execution
            # raise ValueError(f"Driver '{builder.driver_config}' could not be initialized.")

    def initialise_setup(self) -> None:
        """
        Sets up the environment for the driver module.

        This method should be called before performing any application
        management operations.
        """
        internal_logger.debug("Initialising setup for AppManagement.")

    def launch_app(self, app_identifier: Optional[str] = None, app_activity: Optional[str] = None, event_name: Optional[str] = None) -> str:
        """
        Launches the specified application.

        :param event_name: The event triggering the app launch, if any.
        """
        return self.driver.launch_app(
            app_identifier=app_identifier,
            app_activity=app_activity,
            event_name=event_name,
        )

    def start_appium_session(self, event_name: Optional[str] = None) -> None:
        """
        Starts an Appium session.

        :param event_name: The event triggering the session start, if any.
        """
        self.driver.launch_app(event_name)

    def get_driver_session_id(self) -> Optional[str]:
        """Return the current driver session id, if available."""
        return self.driver.get_driver_session_id()

    def launch_other_app(self, app_name: str, event_name: Optional[str] = None) -> None:
        """
        Starts another application.

        :param package_name: The package name of the application.
        :param event_name: The event triggering the app start, if any.
        """
        self.driver.launch_other_app(app_name, event_name)

    def close_and_terminate_app(self) -> None:
        """
        Closes and terminates a specified application.

        :param package_name: The package name of the application.
        :param event_name: The event triggering the app termination, if any.
        """
        self.driver.terminate()

    def force_terminate_app(self, app_name: str, event_name: Optional[str] = None) -> None:
        """
        Forcefully terminates the specified application.

        :param app_name: The name of the application to terminate.
        :param event_name: The event triggering the forced termination, if any.
        """
        self.driver.force_terminate_app(app_name, event_name)

    def get_app_version(self) -> Optional[str]:
        """
        Gets the version of the application.

        :return: The version of the application, or None if not available.
        """
        return self.driver.get_app_version()
